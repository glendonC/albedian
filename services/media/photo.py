"""A still restored end to end, in the job's order (spec restoration-for-every-upload, design overview steps 1 to 9;
REQ-RST-6, REQ-RST-4.3 to 4.6) [decided 2026-11-12 by the lead: the photo path in the job's order is product code,
and the job of restore-2b calls it].

decode -> stars and kept regions -> noise table -> edge-avoiding filter and the first scale smoothed outside the kept
cores -> coarse-scale chroma smoothing -> lift solved -> dissolve passes -> lift applied and encoded at 16 bits -> the
gate on the stored file read back. A `ready` result carries the stored bytes; a `rejected` one carries none: its file is
never written, served or listed. Pure: reads no file but what the decoded still holds, writes none, and draws nothing by
chance.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import ndimage

from services.contracts.media import GRAIN_CAPPED_BASIS, GRAIN_KEPT_BASIS, GRAIN_REFUSED_BASIS
from services.media import bars as bars_rule
from services.media import chroma, decode, encode, gate, noise, png16, spatial, stars, tone

STEP_VERSION = "1.0.0"
NOTE_DISSOLVED = "Points the original does not confirm were dissolved before the gate."
NOTE_CLIPPED = "Clipped regions were left out of the gate."
# The gate measured no star's light, so its flux check did not run (UIUX's words, gate-flux-floor-one)
NOTE_FLUX_NOT_CHECKED = "No star could be measured, so the gate did not check how bright the stars stayed."
DESIGN_7 = "Proposed in design section 7."
# The share of the photo's own finest noise the sky keeps after the first-scale smoothing (`kept_grain`): with none a
# restored sky posterises at 8 and 10 bits and each kept star wears a collar of the original's grain; less of the
# grain's star-shaped part, which the gate would count, so its points fall by four fifths; a still whose gate refuses
# the grained result restores again without it [decided 2026-11-24 20:23 and 22:19 by the lead, fine-noise-floor-one;
# tested: fine-noise-floor-probe and grain-gate-one, the 60 public design crops and rp-b-12]
FINE_NOISE_SHARE = 0.35
# The blocks on which the grain is capped: the window the stated sensitivity is measured in (32 px either side of a
# place, `scripts/restore_review.stated_peak`) [tested: on the gate's 256 px tiles a 4-code star still passed at 5
# places of the public corpus whose 64 px windows stood 1.7 to 2.5 times the floor; on 64 px blocks, at none more than
# main's own, .lanetools/floor/passes.py and built.py]
GRAIN_BLOCK_PX = 64
# Halvings of a block's share after its solve before a block still over the floor keeps no grain: the solve lands a
# block at the floor from above, give or take the stored codes' rounding, and half the share is sure to be under [tested:
# the test suite's scene and the public corpus, .lanetools/floor/built.py]
GRAIN_CAP_ROUNDS = 3
# A painted place lies this far clear of every kept region and clipped region [proposed in design section 7]
PAINT_CLEARANCE_PX = 12
# and this far inside the frame's edges [proposed: past the detector's 4 px border and the patch's own reach]
PAINT_MARGIN_PX = 32
# The painted places' lattice is no finer than this [proposed: one place per tile's worth of frame at most]
PAINT_MIN_SPACING_PX = 48
# A painted patch is drawn this far from its centre, 6 of its sigma [proposed: past that it adds under 1e-7 of its peak]
PATCH_REACH_PX = 8
# Places beside the original's own stars, from inside a star's kept disc out to where the lattice's clearance begins
# [proposed for restore-4e: AUDIT-B's finding A, 2026-11-19, painted at 3, 4 and 6 px]
NEAR_STAR_PX = (3, 4, 5, 6, 8, 10, 12)
# each along this many directions, 45 degrees apart [proposed: at 12 px they lie 9 px apart, under the patch's reach]
NEAR_STAR_DIRECTIONS = 8


class PhotoError(Exception):
    """A still the path cannot restore honestly: the job stores its internal-error sentence."""


class PhotoRefusal(Exception):
    """A still this path refuses before restoring, with a fixed sentence of the contract (`FAILURE_SENTENCES`)."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


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
    # The restoration's tile searches, which the gate answers each tile of the same values from (`gate.Searches`)
    searches: gate.Searches = field(default_factory=gate.Searches)

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
    stored: np.ndarray  # the whole stored frame, its bars included
    measured: dict
    gate: dict
    rejection: Optional[str]
    steps: list[dict]
    context: Context  # of the picture inside the bars, which the gate read
    seconds: dict = field(default_factory=dict)
    unconfirmed: tuple = ()
    bars: bars_rule.Bars = bars_rule.Bars()

    @property
    def picture(self) -> np.ndarray:
        """The stored samples inside the bars: what the gate read."""
        return self.bars.crop(self.stored) if self.bars else self.stored


@dataclass(frozen=True)
class Verdict:
    gate: dict
    unconfirmed: list[stars.Point]


def _parameter(name: str, value, origin: str = "default", basis: str = DESIGN_7) -> dict:
    return {"name": name, "value": value, "origin": origin, "basis": basis}


def _settings(**values) -> list[dict]:
    """The gate's settings as graph parameters (restore-4c's names), each a default design section 7 gives."""
    return [_parameter(name, value) for name, value in values.items()]


def _encode_parameters() -> list[dict]:
    return [_parameter("container", "png", basis="Proposed in design section 8."),
            _parameter("encoder", "png", basis="Proposed in design section 8."),
            _parameter("bitDepth", 16, basis="Decided by the lead in design section 8."),
            _parameter("pixelFormat", "rgb48le", basis="Decided by the lead in design section 8."),
            _parameter("zlibLevel", png16.ZLIB_LEVEL, basis="Proposed in design section 8.")]


def _blocks_on_black_clip(codes: np.ndarray, blocks: np.ndarray) -> bool:
    """Whether the chosen flat blocks of the darkest quarter sit on the black clip (code 0): at least the noise
    estimator's own 25th-percentile share (`noise.ENERGY_PERCENTILE`) of them lie wholly on code 0 across every
    channel, and the median code across all samples of the chosen blocks is 0 [proposed: zero extra constants;
    reuses `noise.BLOCK_PX` and `noise.ENERGY_PERCENTILE`; tested on `rp-b-06` and synthetic controls]."""
    chosen = _chosen_code_blocks(codes, blocks)
    if not chosen.size:
        return False
    block_peak = chosen.max(axis=(1, 2, 3))
    return bool(np.percentile(block_peak, noise.ENERGY_PERCENTILE) == 0 and np.median(chosen) == 0)


def _chosen_code_blocks(codes: np.ndarray, blocks: np.ndarray) -> np.ndarray:
    """The codes of the chosen blocks, (blocks, `noise.BLOCK_PX`, `noise.BLOCK_PX`, channels), in the noise module's
    row-major block order."""
    chosen_mask = np.asarray(blocks, dtype=bool)
    rows, columns = codes.shape[0] // noise.BLOCK_PX, codes.shape[1] // noise.BLOCK_PX
    cut = codes[: rows * noise.BLOCK_PX, : columns * noise.BLOCK_PX]
    grid = cut.reshape(rows, noise.BLOCK_PX, columns, noise.BLOCK_PX, codes.shape[2]).swapaxes(1, 2)
    return grid.reshape(rows * columns, noise.BLOCK_PX, noise.BLOCK_PX, codes.shape[2])[chosen_mask]


def restore_still(still: decode.DecodedStill, exposure_ev: Optional[float] = None, *,
                  max_passes: int = gate.MAX_PASSES) -> StillRestoration:
    """The still restored and gated. A still with black bars (`bars.find_bars` on each sample's largest channel, black
    at code 0: whole edge rows or columns at 0 in every channel on both opposite edges, each opening the picture) is
    measured, restored and gated inside them, and its stored file keeps its bars as they were, at 0 [decided
    2026-11-24 by the lead, photo-bars-one]: the picture inside restores exactly as the same picture without bars."""
    codes = np.asarray(still.samples)
    found = bars_rule.find_bars([codes.max(axis=-1)], 0)
    if not found:
        return _restore_kept(still, exposure_ev, max_passes)
    inner = _restore_kept(dataclasses.replace(still, samples=found.crop(codes)), exposure_ev, max_passes)
    started = time.perf_counter()
    # Every bar sample is 0 in every channel (`find_bars` at black 0), and 0 is stored as 0 at 16 bits
    stored = found.put_back(np.zeros(codes.shape, dtype=np.uint16), inner.stored)
    data = encode.write_still(stored, profile_without_text(still.icc_profile)) if inner.data is not None else None
    if data is not None:
        stored = encode.read_still(data)
        if not np.array_equal(found.crop(stored), inner.stored):
            raise PhotoError("the stored file does not hold the picture the gate read")
    seconds = dict(inner.seconds)
    seconds["encode"] = seconds.get("encode", 0.0) + time.perf_counter() - started
    return dataclasses.replace(inner, data=data, stored=stored, bars=found, seconds=seconds)


def kept_grain(linear: np.ndarray, working: np.ndarray, share: float) -> np.ndarray:
    """`share` of the photo's own finest noise that the smoothing took out, per channel: the residual (the original's
    light less the working image), its finest B3 scale (`noise.fine_scale`) less that scale's Gaussian at the gate's
    star width (`gate.STAR_SIGMA_PX`), so the grain holds no star shape for the gate to count [decided 2026-11-24
    22:19 by the lead, grain-gate-one]. Nothing is invented: zero wherever the working image is the original."""
    grain = np.asarray(linear, dtype=np.float64) - working
    for c in range(grain.shape[-1]):
        w1 = noise.fine_scale(grain[..., c])
        grain[..., c] = share * (w1 - ndimage.gaussian_filter(w1, gate.STAR_SIGMA_PX))
    return grain


def grain_block_noises(image: np.ndarray, kept: gate.Kept, to_stored) -> np.ndarray:
    """The robust spread (1.4826 x MAD) of the gate's search statistic of `image`'s stored luma on each GRAIN_BLOCK_PX
    block, in codes of 255, the samples the search leaves out (`kept.left_out`) left out; 0 on a block with none."""
    statistic = gate.search_statistic(gate.stored_luma(to_stored(image)), kept.left_out)
    searched = ~np.asarray(kept.left_out, dtype=bool)
    rows, columns = -(-image.shape[0] // GRAIN_BLOCK_PX), -(-image.shape[1] // GRAIN_BLOCK_PX)
    spreads = np.zeros((rows, columns))
    for r in range(rows):
        for c in range(columns):
            block = (slice(r * GRAIN_BLOCK_PX, (r + 1) * GRAIN_BLOCK_PX), slice(c * GRAIN_BLOCK_PX, (c + 1) * GRAIN_BLOCK_PX))
            values = statistic[block][searched[block]]
            if values.size:
                spreads[r, c] = 1.4826 * float(np.median(np.abs(values - np.median(values))))
    return spreads


def capped_grain(working: np.ndarray, grain: np.ndarray, kept: gate.Kept, to_stored, share: float
                 ) -> tuple[np.ndarray, float]:
    """`working` with `grain` at `share`, lowered on each GRAIN_BLOCK_PX block where it would raise the noise of the
    gate's search statistic of the stored luma (`grain_block_noises`) over the detector's floor (`gate.noise_floor()`):
    under the floor a star at the stated sensitivity still reaches twice the threshold, so the gate's promise holds as it
    does without grain [decided 2026-11-24 and 2026-11-25 by the lead, ruling B at the floor]. Each block's noise is
    measured without the grain and with it at `share`; a block over the floor takes the share at which the grain's noise
    and the image's own add to the floor (noises add as squares), and none where it is still over once measured again.
    Returns the image and the least share applied."""
    floor = gate.noise_floor()

    def grained(per_block: np.ndarray) -> np.ndarray:
        full = np.kron(per_block, np.ones((GRAIN_BLOCK_PX, GRAIN_BLOCK_PX)))[: working.shape[0], : working.shape[1]]
        return working + full[..., None] * grain

    own = grain_block_noises(working, kept, to_stored)
    full = grain_block_noises(grained(np.full(own.shape, float(share))), kept, to_stored)
    room = np.maximum(floor**2 - own**2, 0.0)
    added = np.maximum(full**2 - own**2, 1e-30)
    shares = np.where(full <= floor, float(share), float(share) * np.sqrt(np.minimum(room / added, 1.0)))
    for _ in range(GRAIN_CAP_ROUNDS):
        over = grain_block_noises(grained(shares), kept, to_stored) > floor
        if not over.any():
            return grained(shares), float(shares.min())
        shares = np.where(over, shares / 2.0, shares)
    shares = np.where(grain_block_noises(grained(shares), kept, to_stored) > floor, 0.0, shares)
    return grained(shares), float(shares.min())


def _grain_parameters(share: float, refused: bool) -> list[dict]:
    """The spatial step's record of the grain its sky keeps: the share, or 0 where the gate refused the grained result
    (grain-share-zero's row, `keptFineNoiseShare`)."""
    if share:
        return [_parameter("keptFineNoiseShare", share, "default", GRAIN_KEPT_BASIS)]
    return [_parameter("keptFineNoiseShare", 0.0, "measured", GRAIN_REFUSED_BASIS)] if refused else []


def _restore_kept(still: decode.DecodedStill, exposure_ev: Optional[float], max_passes: int) -> StillRestoration:
    """`_restore_picture` keeping `FINE_NOISE_SHARE` of the photo's grain; where the gate refuses that result, once more
    without it, so the person gets exactly the restoration of before fine-noise-floor-one and no photo that restored
    is refused [decided 2026-11-24 22:19 by the lead]. The time both took is the result's."""
    grained = _restore_picture(still, exposure_ev, max_passes, FINE_NOISE_SHARE)
    if grained.status == "ready" or not FINE_NOISE_SHARE or "spatial_denoise" not in {s["kind"] for s in grained.steps}:
        return grained
    plain = _restore_picture(still, exposure_ev, max_passes, 0.0, refused=True)
    seconds = {name: plain.seconds.get(name, 0.0) + grained.seconds.get(name, 0.0)
               for name in {*plain.seconds, *grained.seconds}}
    return dataclasses.replace(plain, seconds=seconds)


def _restore_picture(still: decode.DecodedStill, exposure_ev: Optional[float], max_passes: int,
                     share: float = 0.0, refused: bool = False) -> StillRestoration:
    """`restore_still` on a picture with no bars, keeping `share` of its grain (`kept_grain`) where its noise is
    measured."""
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
             {"kind": "find_stars", "version": stars.STEP_VERSION, "parameters": stars.parameters() + _settings(
                 starMinRadiusPx=gate.STAR_MIN_RADIUS_PX, farRingGapPx=gate.FAR_RING_GAP_PX,
                 farRingWidthPx=gate.FAR_RING_WIDTH_PX)},
             {"kind": "measure_noise", "version": noise.STEP_VERSION, "parameters": noise.parameters()}]
    measured: dict = {"colour": still.source_colour, "stars": field.counts(), "notes": []}
    working = linear
    original_noise = working_noise = None
    if measurement.reason is None:
        blocks = noise.darkest_quarter_blocks(luminance, kept.weight, field.clipped)
        before = noise.fine_noise(luminance, blocks)
        if not math.isfinite(before):
            raise PhotoError("the noise at the dark level could not be measured on both images")
        if before <= 0 and _blocks_on_black_clip(codes, blocks):
            raise PhotoRefusal(NEAR_BLACK_CLIP)
        if before <= 0 or noise.flat_in_codes(codes, blocks):
            # A flat dark band: its noise cannot be measured, so the still takes the not-measured path from here, the
            # kept regions as that path finds them; a band whose edge blocks see the sky within the fine scale's reach
            # stays measured, by the clip's flat test [decided 2026-11-24 13:25, 13:59, 17:23 and 19:36 by the lead,
            # photo-flatband-one]; the whole-frame reasons above are given first, as before
            measurement = noise.NoiseMeasurement("dark_level_flat")
            kept = gate.kept_regions(field, codes, top, luminance, measurement)
    if measurement.reason is None:
        measured["noise"] = measurement.table()
        working = spatial.edge_avoiding(linear, noise.stabilised(luminance, measurement), kept.weight).frame
        working = gate.smooth_first_scale(working, kept)
        steps.append({"kind": "spatial_denoise", "version": spatial.STEP_VERSION, "parameters": spatial.parameters() + _settings(
            keptGrowPx=gate.KEPT_GROW_PX, firstScaleLevels=gate.FIRST_SCALE_LEVELS, brightSkyNoises=gate.BRIGHT_SKY_NOISES,
            brightHaloNoises=gate.BRIGHT_HALO_NOISES, brightLevels=gate.BRIGHT_LEVELS,
            brightBackgroundPercentile=gate.BACKGROUND_PERCENTILE, brightBackgroundPx=gate.BACKGROUND_PX)
            + _grain_parameters(share, refused)})
        spatial_step = steps[-1]
        lap("spatial")
        # Smooth sky colour blotches strictly before dissolve: placed after dissolve, nonlinear lifted luma shifts and
        # clamped samples bypass dissolve and drop gate tile reuse to zero [tested: chroma-measure report]
        chroma_guide = noise.stabilised(working @ np.asarray(stars.LUMA_WEIGHTS), measurement)
        handed_chroma = [working]
        del working
        chroma_result = chroma.smooth_chroma(handed_chroma.pop(), chroma_guide, field.footprint)
        del chroma_guide
        working = chroma_result.frame
        measured["chroma"] = {"heldSamples": chroma_result.kept, "clampedSamples": chroma_result.clamped}
        del chroma_result
        steps.append({"kind": "smooth_chroma", "version": chroma.STEP_VERSION, "parameters": chroma.parameters()})
        after = noise.fine_noise(working @ np.asarray(stars.LUMA_WEIGHTS), blocks)
        original_noise = measurement.luma_fine_sigma_at(tone.dark_level(luminance))
        working_noise = after
        # Both noises are numbers, or the noise bound would drop in silence (restore-3-v2 report, items 3 and 4)
        if not (math.isfinite(after) and math.isfinite(original_noise) and original_noise > 0):
            raise PhotoError("the noise at the dark level could not be measured on both images")
        measured["fineNoiseLeft"] = after / before
        lap("chroma")
    else:
        measured["noiseNotMeasured"] = measurement.reason
    lift = tone.solve_lift(luminance, encode_slope=transfer.luma_encode_slope, decode=transfer.luma_decode,
                           original_noise=original_noise, working_noise=working_noise, exposure_ev=exposure_ev)
    original = gate.Original.of(field)

    def to_stored(light: np.ndarray) -> np.ndarray:
        # The 16 bit codes exactly as they will be stored, kept as uint16 (0.145 GB on a 24 MP photo rather than
        # 0.58 GB float64): the detector reads codes on a grid of steps otherwise than unrounded ones, and half a
        # 16 bit code made a point of SNR 18.9 on photo 1 that no pass saw unrounded [tested: .lanetools/restore-4/rounding.py]
        return transfer.encode16(tone.apply_lift(light, lift.beta))

    def to_original_codes(light: np.ndarray) -> np.ndarray:
        # Each sample by its own channel's table alone, so the dissolve may encode only its points' tiles
        out = transfer.encode(light)
        out *= top
        return out

    lap("lift")
    if share and measurement.reason is None:
        # The grain goes in after the lift is solved on the grainless image, capped tile by tile at the gate's floor
        # (`capped_grain`), and the fine noise left is measured again so the record says what the stored file holds
        working, least = capped_grain(working, kept_grain(linear, working, 1.0), kept, to_stored, share)
        measured["fineNoiseLeft"] = noise.fine_noise(working @ np.asarray(stars.LUMA_WEIGHTS), blocks) / before
        if least < share:  # the record carries the share asked, with the basis that says the cap lowered it in places
            spatial_step["parameters"][-1] = _parameter("keptFineNoiseShare", share, "default", GRAIN_CAPPED_BASIS)
    expected = to_stored(linear)
    searches = gate.Searches()
    # Each whole-image array is let go once nothing after it reads it (a 24 MP photo's is 0.58 GB): the dissolve
    # takes the working image as its own, so no name here keeps the image a pass replaces
    handed = [working]
    del working
    dissolved = gate.dissolve(handed.pop(), linear=linear, to_stored=to_stored, to_original_codes=to_original_codes,
                              original=original, kept=kept, max_passes=max_passes, searches=searches,
                              expected=expected, codes_by_tile=True)
    del linear
    lap("dissolve")
    steps.append({"kind": "dissolve_unconfirmed", "version": gate.STEP_VERSION, "parameters": [
        _parameter("maxPasses", max_passes), _parameter("variantThresholdSnr", gate.DISSOLVE_THRESHOLD_SNR),
        _parameter("statistic", gate.STATISTIC)] + _settings(
        sensitivityPeakCodes=gate.SENSITIVITY_PEAK_CODES, floorMargin=gate.FLOOR_MARGIN,
        gateThresholdSnr=gate.VARIANT_THRESHOLD_SNR, fillLevels=gate.DISSOLVE_FILL_LEVELS,
        discMinRadiusPx=gate.DISSOLVE_MIN_RADIUS_PX, discGrowPx=gate.DISSOLVE_GROW_PX, discEdgePx=gate.DISSOLVE_EDGE_PX,
        nearCorePx=gate.NEAR_CORE_PX, unchangedRadiusPx=gate.UNCHANGED_RADIUS_PX,
        unchangedToleranceCodes=gate.UNCHANGED_TOLERANCE_CODES16)})
    steps.append({"kind": "lift", "version": tone.STEP_VERSION, "parameters": lift.parameters()})
    steps.append({"kind": "encode", "version": STEP_VERSION, "parameters": _encode_parameters()})
    per_pass = dissolved.per_pass
    lifted = tone.apply_lift(dissolved.working, lift.beta)
    del dissolved
    stored = transfer.encode16(lifted)
    del lifted
    # Every kept region stored as the gate computes it: a core the original's, an edge the original blended with the
    # stored sky around it (restore-4f)
    stored = gate.kept_samples(stored, expected, kept.weight).astype(np.uint16)
    data = encode.write_still(stored, profile_without_text(still.icc_profile))
    del stored
    read_back = encode.read_still(data)
    lap("encode")
    context = Context(original, kept, transfer, lift.beta, top, luminance, expected, searches)
    verdict = check_full(context, read_back)
    lap("gate")
    measured["dissolvedPerPass"] = per_pass
    measured["lift"] = lift.record()
    notes = list(lift.notes)
    if per_pass:
        notes.append(NOTE_DISSOLVED)
    if field.clipped.any():
        notes.append(NOTE_CLIPPED)
    if verdict.gate["framesFluxNotChecked"]:
        notes.append(NOTE_FLUX_NOT_CHECKED)
    measured["notes"] = notes
    ready = verdict.gate["passed"]
    rejection = None if ready else ("unconfirmed_points" if verdict.unconfirmed else "star_flux_changed")
    return StillRestoration("ready" if ready else "rejected", data if ready else None, read_back, measured,
                            verdict.gate, rejection, steps, context, seconds, tuple(verdict.unconfirmed))


def check_full(context: Context, stored: np.ndarray) -> Verdict:
    """The gate on a whole stored file."""
    outcome = gate.run_gate(stored, original=context.original, kept=context.kept, expected=context.expected,
                            original_luminance=context.original_luminance,
                            variant_luminance=context.variant_luminance(stored), searches=context.searches)
    return Verdict(outcome.record, outcome.unconfirmed)


def check(result: StillRestoration, stored: np.ndarray) -> Verdict:
    """The gate on another stored file of the same original: a painted trial. A whole frame of a barred still is read
    inside its bars, as the gate read the restoration."""
    if result.bars and np.shape(stored) == result.stored.shape:
        stored = result.bars.crop(stored)
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


def near_star_places(result: StillRestoration, count: int) -> list[tuple[int, int, int, int]]:
    """Places (x, y, star index, distance px) beside `count` of the original's stars, picked evenly along the stars
    that lie PAINT_MARGIN_PX inside the frame: at each of NEAR_STAR_PX along NEAR_STAR_DIRECTIONS directions, rounded to
    a sample, every place inside the frame's margin and outside every clipped region (the gate leaves those out)."""
    field = result.context.field
    height, width = field.clipped.shape
    inside = [i for i, s in enumerate(field.stars)
              if PAINT_MARGIN_PX <= s.x_px < width - PAINT_MARGIN_PX and PAINT_MARGIN_PX <= s.y_px < height - PAINT_MARGIN_PX]
    if len(inside) > count:
        inside = [inside[i] for i in np.linspace(0, len(inside) - 1, count).round().astype(int)]
    places = []
    for index in inside:
        star = field.stars[index]
        for distance in NEAR_STAR_PX:
            for k in range(NEAR_STAR_DIRECTIONS):
                angle = 2 * math.pi * k / NEAR_STAR_DIRECTIONS
                x, y = int(round(star.x_px + distance * math.cos(angle))), int(round(star.y_px + distance * math.sin(angle)))
                if PAINT_MARGIN_PX <= x < width - PAINT_MARGIN_PX and PAINT_MARGIN_PX <= y < height - PAINT_MARGIN_PX \
                        and not field.clipped[y, x]:
                    places.append((x, y, index, distance))
    return places


class Trials:
    """Painted trials on one restored still, each checked on the tiles its patch touches only: the gate's statistic
    is a sum over 5 x 5 samples and the detector works tile by tile, so the other tiles' points are those of the
    stored file itself, which `check` would find again."""

    def __init__(self, result: StillRestoration):
        self.result = result
        self.picture = result.picture
        self.luma = gate.stored_luma(self.picture)
        self.image = gate.search_statistic(self.luma, result.context.kept.left_out)
        self.grid = stars.tiles(*self.luma.shape)

    def painted_window(self, x: int, y: int, peak16: float) -> tuple[slice, slice, np.ndarray]:
        stored = self.picture
        rows = slice(max(0, y - PATCH_REACH_PX), min(stored.shape[0], y + PATCH_REACH_PX + 1))
        columns = slice(max(0, x - PATCH_REACH_PX), min(stored.shape[1], x + PATCH_REACH_PX + 1))
        yy, xx = np.mgrid[rows, columns].astype(np.float64)
        patch = peak16 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * gate.STAR_SIGMA_PX**2))
        window = np.clip(np.round(stored[rows, columns] + patch[..., None]), 0, encode.CODE_STEPS).astype(np.uint16)
        return rows, columns, window

    def raised_window(self, x: int, y: int, peak16: float, size: int = 1,
                      over_original: bool = True) -> tuple[slice, slice, np.ndarray]:
        """A `size` x `size` block whose first sample is (x, y), raised in every channel so that each sample's luma
        stands `peak16` over the larger of its own and the original's own luma there (`over_original`, the trial a
        raised sample the original does not hold is refused for), or by `peak16` on the stored sample alone
        (AUDIT-B's probe of 2026-11-19), restore-4f."""
        stored = self.picture
        rows = slice(y, min(stored.shape[0], y + size))
        columns = slice(x, min(stored.shape[1], x + size))
        lift = np.zeros(stored[rows, columns].shape[:2])
        if over_original:
            below = gate.stored_luma(self.result.context.expected[rows, columns]) - self.luma[rows, columns]
            lift = np.maximum(below, 0.0) * gate.CODES_PER_8_BIT
        window = np.clip(np.round(stored[rows, columns].astype(np.float64) + (lift + peak16)[..., None]), 0,
                         encode.CODE_STEPS).astype(np.uint16)
        return rows, columns, window

    def refused(self, x: int, y: int, peak16: float) -> tuple[bool, float]:
        """Whether the gate refuses the stored file with a star of `peak16` painted at (x, y), and the largest snr
        found there (`refused_window`)."""
        rows, columns, window = self.painted_window(x, y, peak16)
        return self.refused_window(rows, columns, window, x, y)

    def refused_raised(self, x: int, y: int, peak16: float, size: int = 1, over_original: bool = True) -> bool:
        """Whether the gate refuses the stored file with a `size` x `size` block at (x, y) raised (`raised_window`)."""
        rows, columns, window = self.raised_window(x, y, peak16, size, over_original)
        return self.refused_window(rows, columns, window, x, y)[0]

    def refused_offsets(self, x: int, y: int, peak16: float, offsets: list[tuple[int, int]]) -> bool:
        """Whether the gate refuses the stored file with single samples raised so (`raised_window`) at (x, y) plus
        each (dy, dx) offset (AUDIT-B's restore-4f v2 patterns: partners on both rings)."""
        places = [(x + dx, y + dy) for dy, dx in offsets]
        x0, x1 = min(p[0] for p in places), max(p[0] for p in places) + 1
        y0, y1 = min(p[1] for p in places), max(p[1] for p in places) + 1
        rows, columns = slice(y0, y1), slice(x0, x1)
        window = self.picture[rows, columns].copy()
        for px, py in places:
            _, _, sample = self.raised_window(px, py, peak16, 1)
            window[py - y0, px - x0] = sample[0, 0]
        return self.refused_window(rows, columns, window, x, y)[0]

    def refused_spaced(self, x: int, y: int, peak16: float, step: tuple[int, int], count: int) -> bool:
        """Whether the gate refuses the stored file with `count` single samples raised so (`raised_window`), the
        first at (x, y), each `step` (dy, dx) from the last: a pair or a line of samples apart (AUDIT-B's restore-4f
        verdict)."""
        places = [(x + k * step[1], y + k * step[0]) for k in range(count)]
        x0, x1 = min(p[0] for p in places), max(p[0] for p in places) + 1
        y0, y1 = min(p[1] for p in places), max(p[1] for p in places) + 1
        rows, columns = slice(y0, y1), slice(x0, x1)
        window = self.picture[rows, columns].copy()
        for px, py in places:
            _, _, sample = self.raised_window(px, py, peak16, 1)
            window[py - y0, px - x0] = sample[0, 0]
        return self.refused_window(rows, columns, window, x, y)[0]

    def refused_window(self, rows: slice, columns: slice, window: np.ndarray, x: int, y: int) -> tuple[bool, float]:
        """Whether the gate refuses the stored file with `window` in place of its samples at (rows, columns): for a
        kept region's sample within the edge fill's reach of the change that is not what it must be, for a raised
        sample or small cluster in the change that the original does not confirm, or for an unconfirmed point within
        the match radius of (x, y); and the largest snr found there."""
        context = self.result.context
        kept = context.kept
        height, width = self.luma.shape
        expected = context.expected[rows, columns]
        # A kept edge's samples read the stored sky within the fill's reach: they are recomputed from a window twice
        # that reach around the change, and compared within the reach of it
        reach = 2 * (2**gate.KEPT_EDGE_FILL_LEVELS - 1)
        margin = 2 * reach + 4
        b0, b1 = max(0, rows.start - margin), min(height, rows.stop + margin)
        a0, a1 = max(0, columns.start - margin), min(width, columns.stop + margin)
        stored = self.picture[b0:b1, a0:a1].astype(np.float64)
        stored[rows.start - b0:rows.stop - b0, columns.start - a0:columns.stop - a0] = window
        must = gate.kept_samples(stored, context.expected[b0:b1, a0:a1], kept.weight[b0:b1, a0:a1])
        inner = (slice(max(0, rows.start - reach) - b0, min(height, rows.stop + reach) - b0),
                 slice(max(0, columns.start - reach) - a0, min(width, columns.stop + reach) - a0))
        region = kept.left_out[b0:b1, a0:a1][inner]
        kept_changed = bool(region.any() and np.any(np.abs(stored[inner][region] - must[inner][region])
                                                    > gate.UNCHANGED_TOLERANCE_CODES16))
        # A raised sample or cluster: its ring is 2 px around it, read from the same window
        luma_window = gate.stored_luma(stored)
        places = gate.raised_places(luma_window, gate.stored_luma(context.expected[b0:b1, a0:a1]),
                                    kept.left_out[b0:b1, a0:a1], context.field.clipped[b0:b1, a0:a1])
        inside = [p for p in places if rows.start <= p.y_px + b0 < rows.stop and columns.start <= p.x_px + a0 < columns.stop]
        raised = False
        if inside:
            framed = [stars.Point(p.x_px + a0, p.y_px + b0, p.snr, p.tile) for p in inside]
            shifted = [stars.Point(p.x_px - columns.start, p.y_px - rows.start, p.snr, p.tile) for p in framed]
            same = gate.unchanged(shifted, window, expected, kept.left_out[rows, columns])
            by_star = gate.on_a_star(framed, context.original)
            raised = any(not (s or b) for s, b in zip(same, by_star))
        # The statistic at a sample reads the luma 2 px around it: recompute it 4 px around the patch from a luma
        # window 8 px around it, whose cut edges lie 4 px or more from what is kept (a frame edge is mirrored alike)
        r0, r1 = max(0, rows.start - 8), min(height, rows.stop + 8)
        c0, c1 = max(0, columns.start - 8), min(width, columns.stop + 8)
        luma = self.luma[r0:r1, c0:c1].copy()
        luma[rows.start - r0:rows.stop - r0, columns.start - c0:columns.stop - c0] = gate.stored_luma(window)
        local = gate.search_statistic(luma, kept.left_out[r0:r1, c0:c1])
        k0, k1 = max(0, rows.start - 4), min(height, rows.stop + 4)
        j0, j1 = max(0, columns.start - 4), min(width, columns.stop + 4)
        image = self.image.copy()
        image[k0:k1, j0:j1] = local[k0 - r0:k1 - r0, j0 - c0:j1 - c0]
        only = {i for i, tile in enumerate(self.grid)
                if tile.x0 < columns.stop and columns.start < tile.x1 and tile.y0 < rows.stop and rows.start < tile.y1}
        points, _ = gate.points_on(image, context.field.clipped, only_tiles=only)
        near = [p for p in points if math.hypot(p.x_px - x, p.y_px - y) <= stars.MATCH_RADIUS_PX]
        if not near:
            return kept_changed or raised, 0.0
        shifted = [stars.Point(p.x_px - columns.start, p.y_px - rows.start, p.snr, p.tile) for p in near]
        same = gate.unchanged(shifted, window, expected, kept.left_out[rows, columns])
        by_star = gate.on_a_star(near, context.original)
        unconfirmed = sum(not (s or b) for s, b in zip(same, by_star))
        return unconfirmed > 0 or kept_changed or raised, max(p.snr for p in near)


# =============================================================================================
# The stored file's colour profile: its colour data kept, its text not (REQ-RST-9.4; restore-8b)
# =============================================================================================

import hashlib  # noqa: E402
import struct  # noqa: E402

# The fixed text of the two text tags a profile keeps [proposed: plain words for the lead or the design lane to change;
# no reader of the app shows them]
PROFILE_DESCRIPTION = "Colour profile of the original"
# A restoration's refusal of a photo whose profile the rebuild cannot carry (review row C20, decided 2026-11-19)
PROFILE_NOT_CARRIED = "Your photo's colour profile cannot be carried into a restored copy, so it was not restored."
# A restoration's refusal of a near-black photo whose darkest quarter sits on the black clip so its dark-level noise
# cannot be measured [decided 2026-11-23 by the lead: review row C36, UIUX-nearblackrestore-READY.md]
NEAR_BLACK_CLIP = "This photo's sky is so close to black that its faintest noise is cut off, so it was not restored."
PROFILE_TEXT = "Text removed by Albedian"
# The header's bytes, the tag count after them [tested: Little CMS's sRGB and Apple's Display P3 read whole so]
_ICC_HEADER = 128
# The tags a profile is rebuilt from, in the order they are written, each with the types it may have [inference: the
# matrix and tone curve model of an RGB or grey display profile as the ICC specification has it, as recalled; tested:
# Little CMS turns colours identically through the rebuilt sRGB]. Single XYZ numbers: the white and black points and
# the three primaries; tone curves; the chromatic adaptation matrix; the chromaticities; the code points. The
# luminance (lumi) is left behind: the absolute luminance of an emissive device, which no transform here reads
# [verified: ICC.1:2022 section 9.2.33, luminanceTag] (restore-8d, AUDIT-B's verdicts/restore-8b-v3.md).
_ICC_COLOUR_TAGS = (
    (b"wtpt", (b"XYZ ",)), (b"bkpt", (b"XYZ ",)), (b"chad", (b"sf32",)),
    (b"rXYZ", (b"XYZ ",)), (b"gXYZ", (b"XYZ ",)), (b"bXYZ", (b"XYZ ",)),
    (b"rTRC", (b"curv", b"para")), (b"gTRC", (b"curv", b"para")), (b"bTRC", (b"curv", b"para")),
    (b"kTRC", (b"curv", b"para")), (b"chrm", (b"chrm",)), (b"cicp", (b"cicp",)),
)
# What a profile must hold to be rebuilt: an RGB one its white point, primaries and curves, a grey one its white
# point and curve
_ICC_NEEDED = {b"RGB ": (b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC"), b"GRAY": (b"wtpt", b"kTRC")}
# The tags a transform of each space reads, every other left behind whatever its type (restore-8d: a grey curve on
# an RGB profile or primaries on a grey one carried bytes no reader of that space looks at)
_ICC_SPACE_TAGS = {
    b"RGB ": frozenset({b"wtpt", b"bkpt", b"chad", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"chrm", b"cicp"}),
    b"GRAY": frozenset({b"wtpt", b"bkpt", b"chad", b"kTRC", b"cicp"}),
}
# The channels of each space, which its chromaticities must number [verified: ICC.1:2022 section 10.2, chromaticityType:
# the number of device channels, then one CIE xy pair for each; tested: Little CMS's sRGB numbers three]
_ICC_CHANNELS = {b"RGB ": 3, b"GRAY": 1}
# The connection space's illuminant every header holds, D50 as an XYZ number, written whatever the original holds there
# (restore-8d) [verified: ICC.1:2022 (version 4.4) and ICC.1:2010 (4.3) section 7.2.16, "PCS illuminant field", D50's
# X 0.9642, Y 1.0, Z 0.8249; ICC.1:2001-04 (2.4) Annex A.1, the field "must be set to the CIE Illuminant D50"; read
# from color.org's PDFs on 2026-11-19; tested: Little CMS writes these bytes in its own sRGB]
_ICC_D50 = bytes.fromhex("0000f6d6" "00010000" "0000d32d")
_ICC_CLASSES = frozenset({b"scnr", b"mntr", b"prtr", b"spac"})
# The parameters of each parametric curve function [tested: Little CMS's sRGB and Apple's Display P3 use function 3
# with five; the other counts are the ICC specification's parametricCurveType as recalled, an inference]
_PARA_COUNTS = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}


def _colour_tag(kind: bytes, body: bytes, channels: int) -> Optional[bytes]:
    """The tag written fresh from its colour numbers at the length its type defines, its reserved bytes zero; None
    when the body is too short for its type or names a function or count it cannot hold, chromaticities among them
    of other than the space's `channels`."""
    def numbers(start: int, length: int) -> Optional[bytes]:
        return body[start:start + length] if len(body) >= start + length else None

    if kind == b"XYZ ":
        data = numbers(8, 12)
    elif kind == b"sf32":
        data = numbers(8, 36)  # chad, the 3 by 3 matrix
    elif kind == b"curv":
        count = int.from_bytes(body[8:12], "big") if len(body) >= 12 else None
        entries = numbers(12, 2 * count) if count is not None else None
        data = body[8:12] + entries if entries is not None else None
    elif kind == b"para":
        function = int.from_bytes(body[8:10], "big") if len(body) >= 10 else None
        values = numbers(12, 4 * _PARA_COUNTS[function]) if function in _PARA_COUNTS else None
        data = body[8:10] + bytes(2) + values if values is not None else None
    elif kind == b"chrm":
        count = int.from_bytes(body[8:10], "big") if len(body) >= 10 else None
        values = numbers(12, 8 * count) if count == channels else None
        data = body[8:12] + values if values is not None else None
    else:  # cicp: primaries, transfer, matrix and range, a byte each
        data = numbers(8, 4)
    return kind + bytes(4) + data if data is not None else None


def _text_tag(version: int, sig: bytes, text: str) -> bytes:
    if version >= 4:
        body = text.encode("utf-16-be")
        return b"mluc" + bytes(4) + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(">II", len(body), 28) + body
    if sig == b"cprt":
        return b"text" + bytes(4) + text.encode("ascii") + b"\x00"
    # textDescriptionType: the ASCII text, no Unicode text and no ScriptCode text (its 67 bytes zero)
    ascii_text = text.encode("ascii") + b"\x00"
    return b"desc" + bytes(4) + struct.pack(">I", len(ascii_text)) + ascii_text + struct.pack(">IIHB", 0, 0, 0, 0) + bytes(67)


def profile_without_text(profile: Optional[bytes]) -> Optional[bytes]:
    """The original's ICC profile as a stored file carries it, built anew from its colour data alone: a fresh header
    holding only the original's version, class, colour space, connection space and rendering intent, and the D50
    illuminant (every other byte zero, the id the MD5 of the profile with its flags, intent and id zero [inference: the ICC
    header's profile id, as recalled; tested: Little CMS reads it]), the description and the copyright written fresh
    with the fixed words, and each colour tag of the matrix and curve model of its own space written from its numbers
    at the length its type defines, identical ones shared. Every other byte of the original is left behind: its
    header's maker, model, dates, illuminant and reserved bytes, its other tags (another space's among them), the
    padding inside and between tags. None when there is no profile or one it cannot rebuild so (not RGB or grey, not
    XYZ connected, a needed tag missing or damaged; a damaged optional tag is left out alone): `stored_colour` then has no
    description to give and the stored still is not made [decided 2026-11-19 by the lead at 07:31 and 08:34: the
    colour data stays, nothing else does; restore-8d]."""
    if not profile or len(profile) < _ICC_HEADER + 4:
        return None
    size = int.from_bytes(profile[0:4], "big")
    count = int.from_bytes(profile[128:132], "big")
    version, space = profile[8], profile[16:20]
    # The tag table must fit inside the profile, which also bounds the count of tags
    if (size > len(profile) or profile[36:40] != b"acsp" or 132 + 12 * count > size or version not in (2, 4)
            or profile[12:16] not in _ICC_CLASSES or space not in _ICC_NEEDED or profile[20:24] != b"XYZ "):
        return None
    table_end, found = 132 + 12 * count, {}
    for index in range(count):
        sig, offset, length = struct.unpack(">4sII", profile[132 + 12 * index:144 + 12 * index])
        if sig in found or offset < table_end or offset + length > size:
            return None
        found[sig] = profile[offset:offset + length]
    tags = [(b"desc", _text_tag(version, b"desc", PROFILE_DESCRIPTION)), (b"cprt", _text_tag(version, b"cprt", PROFILE_TEXT))]
    for sig, kinds in _ICC_COLOUR_TAGS:
        body = found.get(sig)
        if body is None or sig not in _ICC_SPACE_TAGS[space]:
            continue
        written = _colour_tag(body[:4], body, _ICC_CHANNELS[space]) if body[:4] in kinds else None
        # A damaged tag, or one of a type it may not have, is left out alone, and a needed one so left out leaves no
        # profile just below [tested: leaving an optional chad or bkpt out changes 0 codes on the 28 real profiles of
        # this laptop that carry one, perceptual and relative] (restore-8d, after the lead's hold of 12:57)
        if written is not None:
            tags.append((sig, written))
    if not all(sig in dict(tags) for sig in _ICC_NEEDED[space]):
        return None
    start = 132 + 12 * len(tags)
    blocks, placed, table = bytearray(), {}, bytearray()
    for sig, data in tags:
        if data not in placed:
            blocks.extend(bytes(-len(blocks) % 4))
            placed[data] = start + len(blocks)
            blocks.extend(data)
        table.extend(sig + struct.pack(">II", placed[data], len(data)))
    blocks.extend(bytes(-len(blocks) % 4))
    header = bytearray(_ICC_HEADER)
    header[8:10], header[12:24], header[36:40] = profile[8:10], profile[12:24], b"acsp"
    intent = int.from_bytes(profile[64:68], "big")
    header[64:68] = struct.pack(">I", intent if intent <= 3 else 0)
    header[68:80] = _ICC_D50
    out = header + struct.pack(">I", len(tags)) + table + blocks
    out[0:4] = struct.pack(">I", len(out))
    zeroed = bytearray(out)
    zeroed[44:48], zeroed[64:68] = bytes(4), bytes(4)
    out[84:100] = hashlib.md5(bytes(zeroed)).digest()
    return bytes(out)


def stored_colour(colour: dict, icc_profile: Optional[bytes]) -> Optional[dict]:
    """The colour description of a stored still written with `profile_without_text(icc_profile)`: the decoded one,
    its `iccProfileSha256` naming the profile the stored file holds, since a record describes the bytes it ships
    [decided 2026-11-19 by the lead at 12:20; restore-8d]. Unchanged when the original has no profile. None when the
    original has one that cannot be rebuilt: no description is then true of a file without it, so the caller fails
    the attempt rather than store it. The attempt's measured colour keeps naming the original's profile, which it
    describes."""
    if not icc_profile:
        return dict(colour)
    kept = profile_without_text(icc_profile)
    if kept is None:
        return None
    return {**colour, "iccProfileSha256": hashlib.sha256(kept).hexdigest()}
