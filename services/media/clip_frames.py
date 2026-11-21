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

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from services.media import clip_io, colour, encode, frame_pool, gate, noise, spatial, stars, temporal, tone
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

class ClipFramesError(Exception):
    """A clip the path cannot restore honestly: the job stores its internal-error sentence."""


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

    def stored(self, planes: Sequence[np.ndarray]) -> np.ndarray:
        """H x W x 1: the frame as the gate compares it, its Y' plane on the scale of 16 bit codes (a still's stored
        codes), so the gate's tolerance of one such code asks for equal Y' codes. Luma alone: a 4:2:0 chroma sample
        covers two by two pixels, and at a kept core's edge it holds light from outside the core, which the merge and
        the filter change [proposed]; a painted star is in the luma."""
        y = np.asarray(planes[0], dtype=np.float64)
        return (y * self.stored_step())[..., None]

    def stored_step(self) -> float:
        """One code of the stream's Y' in the stored form's 16 bit codes (`stored`): 257 at 8 bits."""
        return encode.CODE_STEPS / (2**self.bits - 1)

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
    # The fine noise after the spatial filter over before, at the darkest quarter of the measure frames' blocks; None
    # without a noise table
    fine_noise_left: Optional[float] = None


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
    original_noise = working_noise = left = None
    if measurement.reason is None:
        working = np.concatenate([_filtered(clip, light, foot, measurement) for light, foot in zip(linear, footprints)], axis=0)
        blocks = noise.darkest_quarter_blocks(luminance, footprint, clipped_map)
        original_noise = measurement.luma_fine_sigma_at(tone.dark_level(luminance))
        working_noise = noise.fine_noise(working @ np.asarray(LUMA), blocks)
        before = noise.fine_noise(luminance, blocks)
        # Both noises are numbers, or the noise bound would drop in silence (as photo.py)
        if not (math.isfinite(before) and before > 0 and math.isfinite(working_noise) and math.isfinite(original_noise)
                and original_noise > 0):
            raise ClipFramesError("the noise at the dark level could not be measured on both images")
        left = working_noise / before
    lift = tone.solve_lift(luminance, encode_slope=clip.encode_slope, decode=clip.decode, original_noise=original_noise,
                           working_noise=working_noise, exposure_ev=exposure_ev)
    return ClipSettings(clip, measurement, lift, len(sampled), found, confirmed, left)


def _filtered(clip: ClipColour, light: np.ndarray, footprint: np.ndarray, measurement: noise.NoiseMeasurement) -> np.ndarray:
    """The spatial filter of design section 5 on one frame, guided by its noise-stabilised luminance."""
    guide = noise.stabilised(light @ np.asarray(LUMA), measurement)
    return spatial.edge_avoiding(light, guide, footprint).frame


# ---------------------------------------------------------------------------------------------------------------
# Each frame
# ---------------------------------------------------------------------------------------------------------------


def stored_luma(stored: np.ndarray) -> np.ndarray:
    """The gate's luma of a frame's stored form (`ClipColour.stored`), on the scale of 255."""
    return np.asarray(stored, dtype=np.float64)[..., 0] / gate.CODES_PER_8_BIT


@dataclass(frozen=True)
class OriginalSide:
    """What frame k's original pixels say, rebuilt alike by the pipeline and by the gate (design section 10): its
    linear light, its stars and those its original frames together confirm (`gate.clip_stars`), the regions kept as it
    recorded them, the frames together, and the registered window the merge uses."""

    original: np.ndarray
    found: gate.ClipStars
    kept: gate.Kept
    together: Optional[gate.Together]
    window: Optional[temporal.Window]
    window_start: int
    # Frame k's own stars before guard 2 (`gate.original_stars` on its luma), for the gate pass (restore-7s)
    own: Optional[stars.StarField] = None

    @property
    def neighbours(self) -> int:
        return 0 if self.window is None else len(self.window.neighbours)

    def confirmer(self) -> gate.Original:
        return gate.Original.of_clip(self.found, self.together)


@dataclass(frozen=True)
class Prepared:
    """A frame before the dissolve passes and the lift: made with its neighbours and filtered, linear light; what its
    original pixels say."""

    working: np.ndarray
    side: OriginalSide

    @property
    def original(self) -> np.ndarray:
        return self.side.original

    @property
    def field(self) -> stars.StarField:
        return self.side.found.field

    @property
    def neighbours(self) -> int:
        return self.side.neighbours


@dataclass(frozen=True)
class FrameResult:
    """A finished frame: its index in the clip, its codes for the encoder, how many neighbours helped, its stars (its
    own, found and confirmed; those only its frames together confirm; those refused as fixed to the sensor), each
    dissolve pass's count, and the lift it was given (the clip's one)."""

    index: int
    planes: tuple[np.ndarray, np.ndarray, np.ndarray]
    neighbours: int
    stars_found: int
    stars_confirmed: int
    beta: float
    together: int = 0
    fixed: int = 0
    dissolved: list = field(default_factory=list)
    # Each closure error of its window, by the clip index of the triple's first frame
    closure: tuple = ()
    # Its samples that are codec copies of the frame before while the scene moved, and its samples (temporal.codec_copies)
    repeats: int = 0
    samples: int = 0
    clipped: bool = False
    # Its own stars as found on its original luma, for the gate pass (restore-7s)
    own_stars: Optional[gate.OwnStars] = None


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


def original_side(settings: ClipSettings, frames: _RunFrames, centre: int,
                  own_stars: Optional[gate.OwnStars] = None) -> OriginalSide:
    """Frame `centre` of a run as its original pixels say: with a noise table, its window registered (codec copies found
    against each frame's previous one) and its frames together; criterion 1's stars on its own luma and those the frames
    together confirm, past both guards (`gate.clip_stars`); the regions kept as it recorded them (`gate.kept_regions`).
    The same for the pipeline and for the gate, from original pixels alone: frame k's own stars, when the restore
    pass found them on this very luma (`own_stars`, its digest checked), are rebuilt from that record, as finding them
    again gives (restore-7s)."""
    clip = settings.colour
    original = frames.linear[centre]
    luma = frames.luma[centre]
    luminance = original @ np.asarray(LUMA)
    window = together = None
    low = centre
    if settings.measurement.reason is None:
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
        luminances = [frames.linear[i] @ np.asarray(LUMA) for i in range(low, high)]
        together = gate.Together.of(frames.luma[low:high], luminances, centre - low, window)
    own = None if own_stars is None else gate.star_field_from(own_stars, luma, clip.peak_code())
    own = gate.original_stars(luma, clip.peak_code()) if own is None else own
    found = gate.clip_stars(luma, clip.peak_code(), together, own)
    measurement = settings.measurement if settings.measurement.reason is None else None
    kept = gate.kept_regions(found.field, luma, clip.peak_code(), luminance, measurement)
    return OriginalSide(original, found, kept, together, window, low, own)


def prepare_frame(settings: ClipSettings, frames: _RunFrames, centre: int) -> Prepared:
    """Frame `centre` of a run before the dissolve passes, in the photo path's order (`photo.py`): with a noise table,
    made with its registered neighbours, its kept regions taking its own original samples back, the spatial filter
    around them and around the stars only its frames together confirm, and the first scale smoothed outside the kept
    cores; without one, the frame as recorded (REQ-RST-4.3)."""
    side = original_side(settings, frames, centre)
    if settings.measurement.reason is not None:
        return Prepared(side.original, side)
    clip, measurement = settings.colour, settings.measurement
    window = side.window
    moved = [temporal.move(frames.linear[centre + n.offset], n.shift) for n in window.neighbours]
    merged = temporal.merge_frame(side.original, moved, [n.usable for n in window.neighbours], measurement).frame
    # What the original recorded stays as it recorded it: the merge's light is taken out of every kept region, so the
    # gate's exact check of a kept core holds (design section 7), as a still's does
    weight = side.kept.weight[..., None]
    start = weight * side.original + (1.0 - weight) * merged
    # A star only the frames together confirm has no footprint, but the filter and the first scale leave it whole: its
    # flux is compared with the frames together's (design section 7)
    protected = gate.Kept(side.found.field, np.maximum(side.kept.weight, side.found.together.footprint))
    guide = noise.stabilised(start @ np.asarray(LUMA), measurement)
    working = spatial.edge_avoiding(start, guide, protected.weight).frame
    return Prepared(gate.smooth_first_scale(working, protected), side)


def _unchanged(prepared: Prepared) -> bool:
    """Whether nothing changed the frame: then every point's stored samples are the original's, which confirms each
    one, and no pass can find a point to dissolve [exact: `gate.unchanged` at a difference of 0]."""
    return prepared.side.together is None and np.array_equal(prepared.working, prepared.original)


def to_stored(settings: ClipSettings, light: np.ndarray) -> np.ndarray:
    """Linear light to the frame as it will be stored, lifted, in the form the gate compares (`ClipColour.stored`)."""
    clip = settings.colour
    return clip.stored(clip.to_planes(tone.apply_lift(light, settings.lift.beta)))


def dissolve_frame(settings: ClipSettings, prepared: Prepared) -> gate.Dissolved:
    """The dissolve passes of design section 7 on a prepared frame, in the codes it will be stored with, against its
    original pixels and its frames together."""
    if _unchanged(prepared):
        return gate.Dissolved(prepared.working, [])
    clip = settings.colour
    return gate.dissolve(prepared.working, linear=prepared.original, to_stored=lambda light: to_stored(settings, light),
                         to_original_codes=lambda light: np.asarray(clip.to_planes(light)[0], dtype=np.float64),
                         original=prepared.side.confirmer(), kept=prepared.side.kept, luma_of=stored_luma)


def finish_frame(settings: ClipSettings, working: np.ndarray, side: OriginalSide) -> tuple[np.ndarray, ...]:
    """The clip's one lift on a frame, back to the stream's own codes, with every kept region's Y' stored as the gate
    computes it (`gate.kept_samples` on the clip's own code step): a core the original's, an edge the original blended
    with the stored sky around it, as a still's (restore-4f)."""
    clip, beta = settings.colour, settings.lift.beta
    planes = clip.to_planes(tone.apply_lift(working, beta))
    if not np.any(side.kept.weight > 0):
        return planes
    step = clip.stored_step()
    must = gate.kept_samples(clip.stored(planes), clip.stored(clip.to_planes(tone.apply_lift(side.original, beta))),
                             side.kept.weight, step)
    y = np.round(must[..., 0] / step).astype(planes[0].dtype)
    return (y, *planes[1:])


def _run_frames(settings: ClipSettings, run: frame_pool.Run, originals: Sequence) -> tuple[_RunFrames, int]:
    """A run's original frames decoded once, and where its own first frame lies among them."""
    if settings.measurement.reason is None:
        # Linear light for every frame a merge reads; the frame before the window only for its codec copies
        need = [run.before - temporal.WINDOW_FRAMES <= k < run.before + run.count + temporal.WINDOW_FRAMES
                for k in range(len(originals))]
        return _RunFrames(settings.colour, originals, need, stacked=True), run.before
    # No merge: the run's own frames are all a frame needs, held alone [tested: restore-7b, the owner's clips]
    own = originals[run.before:run.before + run.count]
    return _RunFrames(settings.colour, own, [True] * len(own), stacked=False), 0


def restore_run(settings: ClipSettings, run: frame_pool.Run) -> list[FrameResult]:
    """The pool's work for one run (`frame_pool.runs` with `window=RUN_WINDOW`): each of its own frames prepared,
    dissolved and finished, in order."""
    frames, first = _run_frames(settings, run, run.frames)
    # The clip index of frames[0]
    origin = run.first - first
    results = []
    for k in range(run.count):
        prepared = prepare_frame(settings, frames, first + k)
        dissolved = dissolve_frame(settings, prepared)
        side = prepared.side
        own, together = side.found.field, side.found.together
        closure = () if side.window is None else tuple(
            (origin + side.window_start + i, error) for i, error in enumerate(side.window.closure_errors))
        repeats, samples = _repeats(settings.colour, run, k)
        results.append(FrameResult(run.first + k, finish_frame(settings, dissolved.working, side), side.neighbours, own.found,
                                   own.confirmed, settings.lift.beta, together.confirmed, side.found.fixed,
                                   list(dissolved.per_pass), closure, repeats, samples, bool(own.clipped.any()),
                                   gate.own_stars_of(side.own, frames.luma[first + k])))
    return results


def _repeats(clip: ClipColour, run: frame_pool.Run, k: int) -> tuple[int, int]:
    """How many samples of the run's own frame `k` are codec copies of the frame before it (`temporal.codec_copies`,
    the scene's motion between them by `temporal.pair_shift`), and its samples; none for the clip's first frame."""
    if run.first + k == 0:
        return 0, 0
    previous, current = run.frames[run.before + k - 1], run.frames[run.before + k]
    between = temporal.pair_shift(clip.luma_codes(previous), clip.luma_codes(current))
    copies = temporal.codec_copies(clip.stacked_codes(previous), clip.stacked_codes(current), between)
    return int(copies.sum()), int(copies.size)


@dataclass(frozen=True)
class FrameGate:
    """The gate on one stored frame against its original pixels: its outcome, and its stars as the record counts them
    (found and confirmed, its own and those only its frames together confirm, and those fixed to the sensor)."""

    index: int
    outcome: gate.GateOutcome
    stars_found: int
    stars_confirmed: int
    together: int
    fixed: int


def gate_frame(settings: ClipSettings, side: OriginalSide, stored_planes: Sequence[np.ndarray]) -> gate.GateOutcome:
    """Design section 7's gate on one stored frame: its points in the stored Y' codes confirmed on its original pixels
    or on its frames together, its kept regions what they must be, and every measured star's flux, the lift undone."""
    clip, beta = settings.colour, settings.lift.beta
    together = side.together
    # The flux is compared with the original lifted and stored as the job stores it, the lift undone (as `expected`
    # for the samples): undoing a lift of 16 on 8 bit codes, the chroma at half resolution again, moved a bright star's
    # core by up to 0.13 of white on the owner's clips where their empty apertures measured 1e-5, so every honest frame
    # was refused [tested: restore-7, .lanetools/restore7_black_gate_diag.py]. A still is stored at 16 bits
    lifted = clip.to_planes(tone.apply_lift(side.original, beta))
    as_stored = tone.undo_lift(clip.to_linear(lifted), beta) @ np.asarray(LUMA)
    return gate.run_gate(clip.stored(stored_planes), original=side.confirmer(), kept=side.kept,
                         expected=clip.stored(lifted), original_luminance=as_stored,
                         variant_luminance=tone.undo_lift(clip.to_linear(stored_planes), beta) @ np.asarray(LUMA),
                         luma_of=stored_luma, code_step=clip.stored_step(), together=side.found.together,
                         together_luminance=None if together is None else together.luminance)


def gate_run(settings: ClipSettings, run: frame_pool.Run) -> list[FrameGate]:
    """The pool's work for the gate: a run of (original planes, stored planes) pairs, or triples with the frame's own
    stars as the restore pass found them (`gate.OwnStars`), each of its own frames gated, in order. The window, the
    frames together and the stars are rebuilt from the original frames alone, by the same rule as the merge's (design
    section 10); a frame's own stars are taken from its record only for the luma they were found on."""
    frames, first = _run_frames(settings, run, [pair[0] for pair in run.frames])
    results = []
    for k in range(run.count):
        item = run.frames[run.before + k]
        side = original_side(settings, frames, first + k, item[2] if len(item) > 2 else None)
        outcome = gate_frame(settings, side, run.frames[run.before + k][1])
        own, together = side.found.field, side.found.together
        results.append(FrameGate(run.first + k, outcome, own.found + together.found, own.confirmed + together.confirmed,
                                 together.confirmed, side.found.fixed))
    return results


__all__ = ["ClipColour", "ClipSettings", "FrameGate", "FrameResult", "OriginalSide", "Prepared", "clip_colour",
           "dissolve_frame", "finish_frame", "gate_frame", "gate_run", "measure_clip", "original_side", "prepare_frame",
           "restore_run", "sample_indices", "stored_luma", "to_stored"]
