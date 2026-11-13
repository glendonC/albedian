"""Every bound of the photo path at its edges (REQ-RST-4.3, 6.1, 6.2; design sections 3 to 6): a sample at each inside
end and one just outside, from one table, so a new bound gets its edges by a row here.

Bounds sampled elsewhere, exactly: the clipped region's growth (test_stars, the map is every sample within
CLIPPED_GROW_PX), the footprint's cosine edge (test_stars, weights 1, 0.5 and 0 at 0, 1 and 2 px beyond the radius),
the footprint's largest radius and the radius rule at one sigma (test_stars), the aperture's smallest radius
(test_stars), the number of empty apertures (test_stars), the lift's maximum (test_tone), the target at its edge
(test_tone) and the dark level's share (test_tone).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from photo_frames import srgb_decode, srgb_encode, srgb_encode_slope
from services.media import noise, spatial, stars, tone

TAKEN = None


def noise_reason(side: int, *, flat_blocks: int | None = None) -> str | None:
    """The noise table's reason (None when measured) for a ramp frame of side x side px, or of 128 x 120 px with
    `flat_blocks` of its 240 blocks left flat (the rest under a footprint)."""
    rng = np.random.default_rng(side)
    height, width = (side, side) if flat_blocks is None else (128, 120)
    intensity = np.tile(np.linspace(0.002, 0.3, width), (height, 1))
    frame = np.stack([intensity + (0.002 + 0.02 * intensity) * rng.standard_normal((height, width)) for _ in range(3)], -1)
    codes = np.round(np.clip(srgb_encode(frame), 0, 1) * 65535).astype(np.uint16)
    footprint = np.zeros((height, width))
    if flat_blocks is not None:
        covered = 240 - flat_blocks
        for k in range(covered):
            row, column = divmod(k, width // noise.BLOCK_PX)
            footprint[row * 8: row * 8 + 8, column * 8: column * 8 + 8] = 1.0
    measured = noise.measure_noise(frame, codes, footprint, np.zeros((height, width), bool), encode_slope=srgb_encode_slope,
                                   code_steps=65535)
    if flat_blocks is not None and measured.reason is None:
        return TAKEN if len(measured.block_counts) == noise.BINS else f"{noise.BINS - len(measured.block_counts)} bin dropped"
    return measured.reason


def side(length: int) -> str | None:
    # A 64 px frame has too few blocks for any bin, so only whether it is too small is judged here
    reason = noise_reason(length)
    return TAKEN if reason != "frame_too_small" else reason


def with_zero_share(share: float) -> str | None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(noise, "_zero_share", lambda codes, outside: share)
        reason = noise_reason(128)
    return TAKEN if reason != "fine_detail_mostly_zero" else reason


def with_fewest_bins(offset: int) -> str | None:
    # The ramp frame keeps all twelve bins; the bound is moved to twelve plus the offset
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(noise, "MIN_BINS", noise.BINS + offset)
        return noise_reason(128)


def tile_count(size: int) -> str | None:
    count = len(stars.tiles(100, size))
    return TAKEN if count == 1 else f"{count} tiles"


def confirmed_at(distance: float) -> str | None:
    # The verified detection is set at a known distance from the point, so only the radius decides
    point = stars.Point(0.0, 0.0, 10.0, 0)
    found = [SimpleNamespace(x_px=distance, y_px=0.0)]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(stars, "verify_candidates_on_original_pixels", lambda part, places, noise_floor: found)
        patch.setattr(stars, "matched_noise", lambda part: 1.0)
        ok = stars.confirm([point], np.zeros((256, 256)))
    return TAKEN if ok == [True] else "not confirmed"


def merged_at(distance: float) -> str | None:
    kept = stars._merged([stars.Point(0.0, 0.0, 9.0, 0), stars.Point(distance, 0.0, 8.0, 0)])
    return TAKEN if len(kept) == 1 else "kept apart"


def empty_place_at(clearance: float) -> str | None:
    # The lattice for radius 3 runs along y = 10, 31, 52, ...; a footprint row `clearance` px above the row at 52
    footprint = np.zeros((200, 200))
    footprint[int(52 - clearance)] = 1.0
    places = stars.empty_places((200, 200), 3, footprint, np.zeros((200, 200), bool))
    return TAKEN if any(y == 52 for _, y in places) else "too close"


def target_reached(ratio: float) -> str | None:
    target = float(srgb_decode(np.array(tone.TARGET_MEDIAN_CODE / tone.TARGET_CODE_STEPS)))
    lift = tone.solve_lift(np.full((64, 64), ratio * target), encode_slope=srgb_encode_slope, decode=srgb_decode,
                           original_noise=None, working_noise=None)
    return TAKEN if lift.decided_by != "none" else "not lifted"


def weight_floor(offset: float) -> str | None:
    # A footprint of 1 - 2^-20 everywhere leaves each tap 2^-20 of its weight; with a constant guide the weights sum
    # to exactly 2^-20, and the floor is set at that sum plus the offset
    rng = np.random.default_rng(15)
    frame = rng.uniform(0, 1, (24, 24, 3))
    keep = 2.0**-20
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(spatial, "WEIGHT_FLOOR", keep + offset)
        out = spatial.edge_avoiding(frame, np.zeros((24, 24)), np.full((24, 24), 1 - keep), levels=1).frame
    smoothed = (1 - keep) * frame + keep * np.stack([noise.b3_smooth(frame[..., c], 1) for c in range(3)], -1)
    kept = (1 - keep) * frame + keep * frame
    if np.abs(out - smoothed).max() < 1e-13:
        return TAKEN
    assert np.abs(out - kept).max() < 1e-13
    return "own value kept"


EDGES = {
    # name: (the call, None when it takes a value and what happens when it does not, values taken, values refused, that)
    "a frame's shorter side (noise.MIN_SIDE_PX)": (side, [noise.MIN_SIDE_PX, 128], [noise.MIN_SIDE_PX - 1], "frame_too_small"),
    "a channel's share of exactly zero fine detail (noise.ZERO_SHARE_MAX)":
        (with_zero_share, [0.0, noise.ZERO_SHARE_MAX], [np.nextafter(noise.ZERO_SHARE_MAX, 1.0)], "fine_detail_mostly_zero"),
    "the blocks behind a bin (noise.MIN_BLOCKS_PER_BIN)":
        (lambda n: noise_reason(0, flat_blocks=n), [noise.BINS * noise.MIN_BLOCKS_PER_BIN], [noise.BINS * noise.MIN_BLOCKS_PER_BIN - 1],
         "1 bin dropped"),
    "the bins left (noise.MIN_BINS)": (with_fewest_bins, [0], [1], "too_few_flat_blocks"),
    "a tile's side (stars.TILE_PX)": (tile_count, [1, stars.TILE_PX], [stars.TILE_PX + 1], "2 tiles"),
    "a confirmation's distance (stars.MATCH_RADIUS_PX)":
        (confirmed_at, [0.0, stars.MATCH_RADIUS_PX], [np.nextafter(stars.MATCH_RADIUS_PX, 9.0)], "not confirmed"),
    "a duplicate's distance (stars.MERGE_RADIUS_PX)":
        (merged_at, [0.0, stars.MERGE_RADIUS_PX], [np.nextafter(stars.MERGE_RADIUS_PX, 9.0)], "kept apart"),
    "an empty aperture's clearance (stars.EMPTY_CLEARANCE_PX)":
        (empty_place_at, [stars.EMPTY_CLEARANCE_PX, 30], [stars.EMPTY_CLEARANCE_PX - 1], "too close"),
    "the median under the target (tone.TARGET_MEDIAN_CODE)": (target_reached, [0.5, 0.999], [1.0, 1.001], "not lifted"),
    "the sum of weights (spatial.WEIGHT_FLOOR)": (weight_floor, [0.0, -(2.0**-30)], [2.0**-40], "own value kept"),
}


@pytest.mark.parametrize("bound", sorted(EDGES))
def test_every_bound_takes_its_inside_ends_and_refuses_just_outside(bound: str) -> None:
    judge, taken, refused, outcome = EDGES[bound]
    assert [judge(value) for value in taken] == [TAKEN] * len(taken)
    assert [judge(value) for value in refused] == [outcome] * len(refused)
