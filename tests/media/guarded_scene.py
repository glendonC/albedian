"""The job scene of P-RST-14, 23 and 24 (guard-one-places-one): a panning clip of 7 frames at 256 px, 8-bit 4:2:0,
drawn afresh for each seed, with its places in the middle frame; read by test_jobs.py (through the job) and by
test_clip_frames.py (over draws, in process). Not a test file: importing it changes no setting."""
from __future__ import annotations

import math

import numpy as np

from services.media import clip_frames, clip_io, stars

# The upload's tags, as test_jobs.py's lossless upload writes them
BT709_TV = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")


# The job scene of P-RST-14, 23 and 24, its places given in the middle frame, each in a registration block of its own
# (`temporal.BLOCK_PX`, 128 px): a star of the scene too faint for one frame (`faint`), a light of the scene only the
# neighbours carry (`neighbours`), a patch on one sensor place in every frame (`sensor`), and a place where nothing is
# (`empty`) [tested: guard-one-places-one; with the faint star at (130.4, 120.7), in the patch's block at its edge, that
# block held for no neighbour in 31 of 100 draws, and there neither the frames together nor guard 2 saw anything]
GUARDED_PLACES = {"faint": (190.4, 180.7), "neighbours": (70.3, 190.6), "sensor": (190.2, 60.4), "empty": (60.6, 70.2)}
# Too faint for one frame alone in every draw measured, kept on the frames together in most [tested: guard-one-places-one,
# `.lanetools/guard1/diag_fixed_apart.jsonl`]
GUARDED_FAINT_PEAK = 0.004
# The draws the rate is asked over, and the least share of the faint star's cases it is kept in [tested: as above]
GUARDED_DRAWS = range(1, 40 + 1)
GUARDED_FAINT_KEPT = 0.9
# A field star of the scene this near the light or the patch, in the middle frame, is that place's own point, and this
# near the faint star it blends with it on the frames together: the draw is set aside for that place [tested:
# guard-one-places-one, of the 9 faint stars of 98 lost at 5 px, 6 had a field star 3.5 to 6.9 px away]
GUARDED_CLEAR_PX = 2 * stars.MATCH_RADIUS_PX
GUARDED_CROWD_PX = 8.0


def guarded_frames(seed: int) -> tuple:
    """The scene's clip colour, its seven frames' planes as the lossless upload stores them, and each place's distance
    to the nearest field star in the middle frame."""
    from test_clip_frames import at_place, pan, render, spot, star_list

    places = GUARDED_PLACES
    rng = np.random.default_rng(seed)
    offsets = pan(rng, frames=7)
    placed = star_list(rng, 400, 2.0 * 256 + 260 + 140)
    clip = clip_frames.clip_colour(clip_io.VideoFormat(256, 256, "yuv420p", BT709_TV))
    frames = []
    for k, o in enumerate(offsets):
        light = render(o, placed, 256, sky=0.03, cloud=0.0) + at_place(places["faint"], o, offsets[3], GUARDED_FAINT_PEAK)
        light = light + at_place(places["neighbours"], o, offsets[3], 0.03) * (k != 3) + spot(*places["sensor"], 0.05)
        frames.append(clip.to_planes(np.clip(np.repeat(light[..., None], 3, axis=-1) + rng.normal(0.0, 0.003, (256, 256, 3)), 0, 1)))
    middle = offsets[3]
    nearest = {name: float(np.min(np.hypot(placed[:, 0] - middle[1] - x, placed[:, 1] - middle[0] - y)))
               for name, (x, y) in places.items()}
    return clip, frames, nearest


def near_any(points, place: tuple[float, float]) -> bool:
    return any(math.hypot(p.x_px - place[0], p.y_px - place[1]) <= stars.MATCH_RADIUS_PX for p in points)


def guarded_side(clip, frames) -> clip_frames.OriginalSide:
    """The middle frame as its original pixels say, the same for the merge and the gate."""
    settings = clip_frames.measure_clip(clip, [frames[i] for i in clip_frames.sample_indices(len(frames))])
    return clip_frames.original_side(settings, clip_frames._RunFrames(clip, frames, [True] * len(frames), True), 3)
