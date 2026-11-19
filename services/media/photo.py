"""A still restored end to end, in the job's order (spec restoration-for-every-upload, design overview steps 1 to 9;
REQ-RST-6, REQ-RST-4.3 to 4.6) [decided 2026-11-12 by the lead: the photo path in the job's order is product code,
and the job of restore-2b calls it].

decode -> stars and kept regions -> noise table -> edge-avoiding filter and the first scale smoothed outside the kept
cores -> lift solved -> dissolve passes -> lift applied and encoded at 16 bits -> the gate on the stored file read
back. A `ready` result carries the stored bytes; a `rejected` one carries none: its file is never written, served or
listed. Pure: reads no file but what the decoded still holds, writes none, and draws nothing by chance.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import ndimage

from services.media import decode, encode, gate, noise, png16, spatial, stars, tone

STEP_VERSION = "1.0.0"
NOTE_DISSOLVED = "Points the original does not confirm were dissolved before the gate."
NOTE_CLIPPED = "Clipped regions were left out of the gate."
DESIGN_7 = "Proposed in design section 7."
# A painted place lies this far clear of every kept region and clipped region [proposed in design section 7]
PAINT_CLEARANCE_PX = 12
# and this far inside the frame's edges [proposed: past the detector's 4 px border and the patch's own reach]
PAINT_MARGIN_PX = 32
# The painted places' lattice is no finer than this [proposed: one place per tile's worth of frame at most]
PAINT_MIN_SPACING_PX = 48
# A painted patch is drawn this far from its centre, 6 of its sigma [proposed: past that it adds under 1e-7 of its peak]
PATCH_REACH_PX = 8


class PhotoError(Exception):
    """A still the path cannot restore honestly: the job stores its internal-error sentence."""


@dataclass(frozen=True)
class Context:
    """What checking a stored file against the original needs."""

    original: gate.Original
    kept: gate.Kept
    transfer: encode.StillTransfer
    beta: float
    top: float
    original_luminance: np.ndarray
    expected: np.ndarray  # the original lifted and stored as the job stores it, 16 bit codes

    @property
    def field(self) -> stars.StarField:
        return self.kept.field

    @property
    def protected(self) -> np.ndarray:
        return self.kept.weight

    def variant_luminance(self, stored: np.ndarray) -> np.ndarray:
        return tone.undo_lift(self.transfer.decode(stored / encode.CODE_STEPS), self.beta) @ np.asarray(stars.LUMA_WEIGHTS)


@dataclass(frozen=True)
class StillRestoration:
    status: str  # "ready" or "rejected"
    data: Optional[bytes]
    stored: np.ndarray
    measured: dict
    gate: dict
    rejection: Optional[str]
    steps: list[dict]
    context: Context
    seconds: dict = field(default_factory=dict)
    unconfirmed: tuple = ()


@dataclass(frozen=True)
class Verdict:
    gate: dict
    unconfirmed: list[stars.Point]


def _parameter(name: str, value, origin: str = "default", basis: str = DESIGN_7) -> dict:
    return {"name": name, "value": value, "origin": origin, "basis": basis}


def _encode_parameters() -> list[dict]:
    return [_parameter("container", "png", basis="Proposed in design section 8."),
            _parameter("encoder", "png", basis="Proposed in design section 8."),
            _parameter("bitDepth", 16, basis="Decided by the lead in design section 8."),
            _parameter("pixelFormat", "rgb48le", basis="Decided by the lead in design section 8."),
            _parameter("zlibLevel", png16.ZLIB_LEVEL, basis="Proposed in design section 8.")]


def restore_still(still: decode.DecodedStill, exposure_ev: Optional[float] = None, *,
                  max_passes: int = gate.MAX_PASSES) -> StillRestoration:
    seconds: dict = {}
    clock = [time.perf_counter()]

    def lap(name: str) -> None:
        now = time.perf_counter()
        seconds[name] = seconds.get(name, 0.0) + now - clock[0]
        clock[0] = now

    codes = np.asarray(still.samples)
    top = 255.0 if codes.dtype == np.uint8 else float(encode.CODE_STEPS)
    transfer = encode.StillTransfer.from_still(still)
    linear = transfer.decode(codes / top)
    if not np.all(np.isfinite(linear)):
        raise PhotoError("the decoded light is not finite")
    luminance = linear @ np.asarray(stars.LUMA_WEIGHTS)
    lap("decode")
    field = gate.original_stars(codes, top)
    lap("stars")
    measurement = noise.measure_noise(linear, codes, field.footprint, field.clipped,
                                      encode_slope=transfer.luma_encode_slope, code_steps=int(top))
    kept = gate.kept_regions(field, codes, top, luminance, measurement)
    lap("noise_and_kept")
    steps = [{"kind": "decode", "version": STEP_VERSION, "parameters": still.parameters},
             {"kind": "find_stars", "version": stars.STEP_VERSION, "parameters": stars.parameters()},
             {"kind": "measure_noise", "version": noise.STEP_VERSION, "parameters": noise.parameters()}]
    measured: dict = {"colour": still.source_colour, "stars": field.counts(), "notes": []}
    working = linear
    original_noise = working_noise = None
    if measurement.reason is None:
        measured["noise"] = measurement.table()
        guide = noise.stabilised(luminance, measurement)
        working = spatial.edge_avoiding(linear, guide, kept.weight).frame
        working = gate.smooth_first_scale(working, kept)
        steps.append({"kind": "spatial_denoise", "version": spatial.STEP_VERSION, "parameters": spatial.parameters()})
        blocks = noise.darkest_quarter_blocks(luminance, kept.weight, field.clipped)
        before = noise.fine_noise(luminance, blocks)
        after = noise.fine_noise(working @ np.asarray(stars.LUMA_WEIGHTS), blocks)
        original_noise = measurement.luma_fine_sigma_at(tone.dark_level(luminance))
        working_noise = after
        # Both noises are numbers, or the noise bound would drop in silence (restore-3-v2 report, items 3 and 4)
        if not (math.isfinite(before) and before > 0 and math.isfinite(after) and math.isfinite(original_noise)
                and original_noise > 0):
            raise PhotoError("the noise at the dark level could not be measured on both images")
        measured["fineNoiseLeft"] = after / before
        lap("spatial")
    else:
        measured["noiseNotMeasured"] = measurement.reason
    lift = tone.solve_lift(luminance, encode_slope=transfer.luma_encode_slope, decode=transfer.luma_decode,
                           original_noise=original_noise, working_noise=working_noise, exposure_ev=exposure_ev)
    original = gate.Original.of(field)

    def to_stored(light: np.ndarray) -> np.ndarray:
        # The 16 bit codes exactly as they will be stored: the detector reads codes on a grid of steps otherwise than
        # unrounded ones, and half a 16 bit code made a point of SNR 18.9 on photo 1 that no pass saw unrounded
        # [tested: .lanetools/restore-4/rounding.py]
        return transfer.encode16(tone.apply_lift(light, lift.beta)).astype(np.float64)

    def to_original_codes(light: np.ndarray) -> np.ndarray:
        return transfer.encode(light) * top

    lap("lift")
    dissolved = gate.dissolve(working, linear=linear, to_stored=to_stored, to_original_codes=to_original_codes,
                              original=original, kept=kept, max_passes=max_passes)
    lap("dissolve")
    steps.append({"kind": "dissolve_unconfirmed", "version": gate.STEP_VERSION, "parameters": [
        _parameter("maxPasses", max_passes), _parameter("variantThresholdSnr", gate.DISSOLVE_THRESHOLD_SNR),
        _parameter("statistic", gate.STATISTIC)]})
    steps.append({"kind": "lift", "version": tone.STEP_VERSION, "parameters": lift.parameters()})
    steps.append({"kind": "encode", "version": STEP_VERSION, "parameters": _encode_parameters()})
    stored = transfer.encode16(tone.apply_lift(dissolved.working, lift.beta))
    data = encode.write_still(stored, still.icc_profile)
    read_back = encode.read_still(data)
    lap("encode")
    context = Context(original, kept, transfer, lift.beta, top, luminance, transfer.encode16(tone.apply_lift(linear, lift.beta)))
    verdict = check_full(context, read_back)
    lap("gate")
    measured["dissolvedPerPass"] = dissolved.per_pass
    measured["lift"] = lift.record()
    notes = list(lift.notes)
    if dissolved.per_pass:
        notes.append(NOTE_DISSOLVED)
    if field.clipped.any():
        notes.append(NOTE_CLIPPED)
    measured["notes"] = notes
    ready = verdict.gate["passed"]
    rejection = None if ready else ("unconfirmed_points" if verdict.unconfirmed else "star_flux_changed")
    return StillRestoration("ready" if ready else "rejected", data if ready else None, read_back, measured,
                            verdict.gate, rejection, steps, context, seconds, tuple(verdict.unconfirmed))


def check_full(context: Context, stored: np.ndarray) -> Verdict:
    """The gate on a whole stored file."""
    outcome = gate.run_gate(stored, original=context.original, kept=context.kept, expected=context.expected,
                            original_luminance=context.original_luminance,
                            variant_luminance=context.variant_luminance(stored))
    return Verdict(outcome.record, outcome.unconfirmed)


def check(result: StillRestoration, stored: np.ndarray) -> Verdict:
    """The gate on another stored file of the same original: a painted trial."""
    return check_full(result.context, stored)


def painted_places(result: StillRestoration, count: int) -> list[tuple[int, int]]:
    """`count` places (x, y) of a fixed lattice, in row order, PAINT_CLEARANCE_PX clear of every kept region and
    clipped region and PAINT_MARGIN_PX inside the frame, picked evenly along the lattice."""
    occupied = (result.context.protected > 0) | result.context.field.clipped
    clearance = ndimage.distance_transform_edt(~occupied) if occupied.any() else np.full(occupied.shape, np.inf)
    height, width = occupied.shape
    spacing = max(PAINT_MIN_SPACING_PX, int(math.sqrt(height * width / (4 * max(count, 1)))))
    lattice = [(x, y) for y in range(PAINT_MARGIN_PX, height - PAINT_MARGIN_PX, spacing)
               for x in range(PAINT_MARGIN_PX, width - PAINT_MARGIN_PX, spacing) if clearance[y, x] >= PAINT_CLEARANCE_PX]
    if len(lattice) <= count:
        return lattice
    return [lattice[i] for i in np.linspace(0, len(lattice) - 1, count).round().astype(int)]


class Trials:
    """Painted trials on one restored still, each checked on the tiles its patch touches only: the gate's statistic
    is a sum over 5 x 5 samples and the detector works tile by tile, so the other tiles' points are those of the
    stored file itself, which `check` would find again."""

    def __init__(self, result: StillRestoration):
        self.result = result
        self.luma = gate.stored_luma(result.stored)
        self.image = gate.statistic_image(self.luma)
        self.grid = stars.tiles(*self.luma.shape)

    def painted_window(self, x: int, y: int, peak16: float) -> tuple[slice, slice, np.ndarray]:
        stored = self.result.stored
        rows = slice(max(0, y - PATCH_REACH_PX), min(stored.shape[0], y + PATCH_REACH_PX + 1))
        columns = slice(max(0, x - PATCH_REACH_PX), min(stored.shape[1], x + PATCH_REACH_PX + 1))
        yy, xx = np.mgrid[rows, columns].astype(np.float64)
        patch = peak16 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * gate.STAR_SIGMA_PX**2))
        window = np.clip(np.round(stored[rows, columns] + patch[..., None]), 0, encode.CODE_STEPS).astype(np.uint16)
        return rows, columns, window

    def refused(self, x: int, y: int, peak16: float) -> tuple[bool, float]:
        """Whether the gate refuses the stored file with a star of `peak16` painted at (x, y), with an unconfirmed
        point at the patch's place; and the largest snr found there."""
        rows, columns, window = self.painted_window(x, y, peak16)
        height, width = self.luma.shape
        # The statistic at a sample reads the luma 2 px around it: recompute it 4 px around the patch from a luma
        # window 8 px around it, whose cut edges lie 4 px or more from what is kept (a frame edge is mirrored alike)
        r0, r1 = max(0, rows.start - 8), min(height, rows.stop + 8)
        c0, c1 = max(0, columns.start - 8), min(width, columns.stop + 8)
        luma = self.luma[r0:r1, c0:c1].copy()
        luma[rows.start - r0:rows.stop - r0, columns.start - c0:columns.stop - c0] = gate.stored_luma(window)
        local = gate.statistic_image(luma)
        k0, k1 = max(0, rows.start - 4), min(height, rows.stop + 4)
        j0, j1 = max(0, columns.start - 4), min(width, columns.stop + 4)
        image = self.image.copy()
        image[k0:k1, j0:j1] = local[k0 - r0:k1 - r0, j0 - c0:j1 - c0]
        only = {i for i, tile in enumerate(self.grid)
                if tile.x0 < columns.stop and columns.start < tile.x1 and tile.y0 < rows.stop and rows.start < tile.y1}
        points, _ = gate.points_on(image, self.result.context.field.clipped, only_tiles=only)
        near = [p for p in points if math.hypot(p.x_px - x, p.y_px - y) <= stars.MATCH_RADIUS_PX]
        if not near:
            return False, 0.0
        shifted = [stars.Point(p.x_px - columns.start, p.y_px - rows.start, p.snr, p.tile) for p in near]
        same = gate.unchanged(shifted, window, self.result.context.expected[rows, columns])
        by_star = gate.on_a_star(near, self.result.context.original)
        unconfirmed = sum(not (s or b) for s, b in zip(same, by_star))
        return unconfirmed > 0, max(p.snr for p in near)
