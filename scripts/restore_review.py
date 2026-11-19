#!/usr/bin/env python3
"""Restore real photos end to end and write their review: counts, painted-star trials and before and after pictures
(spec restoration-for-every-upload, restore-4; REQ-RST-6.7, 6.12, REQ-RST-12).

Usage:
    uv run python scripts/restore_review.py [--photos rp-b-01 ...] [--corpus] [--crop] [--trials 40]
        [--near-stars 8] [--out .cache/restore-review/restore-4] [--no-pictures]
    uv run python scripts/restore_review.py --clips <clip> ... [--workers 2] [--trials 20] [--near-stars 2]
        [--out .cache/restore-review/restore-7]

Photos come from the real-photo corpus (`research/benchmarks/real_photos/corpus.json`, files in `.cache/real-photos/`);
the default is the design's five (rp-b-01 to rp-b-05), `--corpus` takes every photo. `--crop` takes the design's
768 px crop of the upper middle. For each photo it writes, under the output folder:
- `<id>[-crop].json`: the status, the attempt's measurements, the gate's record, seconds per step and the machine's
  load beside them, and the painted trials: how many places refused a star at the stated sensitivity, at half of
  it, and the least peak in codes of 255 each place refused (`everyTrialCodes`, the stated sensitivity reached);
  and the trials beside the original's own stars (`nearStars`, restore-4e): at 3 to 12 px from each of up to
  `--near-stars` stars along eight directions, how many were refused at the stated sensitivity and at half of it,
  by distance, with every place not refused.
- with pictures: `<id>[-crop]-before.png` and `-after.png` (8 bit views), `-after-16bit.png` (the stored file the
  gate ran on, written only when it is `ready`) and a side by side view no wider than 2400 px.
- `review.jsonl`: one line per photo, appended.

With `--clips` (restore-7), each clip goes through the job's own child function in a scratch store under the output
folder (the clip linked there, never changed, the store removed after), and `clip-<n>.json` holds its status, the
attempt's measurements and gate, the seconds by stage and per frame with the pool's size, the stored bytes per frame,
and painted trials on its middle frame as stored; with pictures, its middle frame before and after, side by side,
and excerpts of the original, of the variant as stored and of the variant at crf 16 for the encoder's comparison.

Nothing here is served: the pictures are for a person to judge (REQ-RST-12.3). No record holds a file name, a
capture time or a place: only the corpus id.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.media import clip_frames, clip_io, decode, frame_pool, gate, photo, tone  # noqa: E402
from services.media import jobs as media_jobs  # noqa: E402

CORPUS = ROOT / "research/benchmarks/real_photos/corpus.json"
DESIGN_PHOTOS = ("rp-b-01", "rp-b-02", "rp-b-03", "rp-b-04", "rp-b-05")
# The design's crop: 768 px of the upper middle (design.md, "Measured facts")
CROP_PX = 768
# The peaks, in codes of 255, a place that refused no star at the stated sensitivity is tried at, rising
LADDER = (5, 6, 8, 10, 12, 16, 24, 32, 48, 64)
SIDE_BY_SIDE_MAX_PX = 2400


def load_average() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip()


def crop(still: decode.DecodedStill) -> decode.DecodedStill:
    samples = still.samples
    height, width = samples.shape[:2]
    y0, x0 = max(0, height // 4 - CROP_PX // 2), max(0, (width - CROP_PX) // 2)
    return dataclasses.replace(still, samples=np.ascontiguousarray(samples[y0:y0 + CROP_PX, x0:x0 + CROP_PX]))


def stated_peak(stored_luma: np.ndarray, x: int, y: int) -> tuple[float, float]:
    """The stated sensitivity at a place (REQ-RST-6.7), in 16 bit codes: 5 pixel noises of the stored luma within
    32 px around it, and at least 4 codes of 255; and that pixel noise, in codes of 255."""
    from scipy import ndimage

    window = stored_luma[max(0, y - 32):y + 32, max(0, x - 32):x + 32]
    residual = window - ndimage.median_filter(window, size=5, mode="nearest")
    pixel = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    return max(gate.SENSITIVITY_PIXEL_NOISES * pixel, gate.SENSITIVITY_PEAK_CODES) * gate.CODES_PER_8_BIT, pixel


def least_refused(trials: photo.Trials, x: int, y: int, peak16: float) -> float | None:
    """The least peak of LADDER, in codes of 255, above `peak16` that a place refuses, or None."""
    for codes in LADDER:
        if codes * gate.CODES_PER_8_BIT > peak16 and trials.refused(x, y, codes * gate.CODES_PER_8_BIT)[0]:
            return float(codes)
    return None


def painted(result: photo.StillRestoration, count: int) -> dict:
    trials = photo.Trials(result)
    places = []
    for x, y in photo.painted_places(result, count):
        peak16, pixel = stated_peak(trials.luma, x, y)
        refused, snr = trials.refused(x, y, peak16)
        half, _ = trials.refused(x, y, 0.5 * peak16)
        least = peak16 / gate.CODES_PER_8_BIT if refused else least_refused(trials, x, y, peak16)
        places.append({"x": x, "y": y, "peak": round(peak16 / gate.CODES_PER_8_BIT, 3), "refused": refused,
                       "refusedAtHalf": half, "snr": round(snr, 2), "leastRefusedCodes": least,
                       "pixelNoiseCodes": round(pixel, 4)})
    every = None if any(p["leastRefusedCodes"] is None for p in places) else max((p["leastRefusedCodes"] for p in places), default=None)
    return {"trials": len(places), "refused": sum(p["refused"] for p in places),
            "refusedAtHalf": sum(p["refusedAtHalf"] for p in places), "everyTrialCodes": every, "places": places}


def near_stars(result: photo.StillRestoration, count: int) -> dict:
    """Trials beside `count` of the original's stars (photo.near_star_places), each at the stated sensitivity at its
    place (`stated_peak`, as the lattice's) and at half of it, refused for an unconfirmed point at the patch or a kept
    core's sample it changed; a place not refused is tried at rising peaks, and the least refused is recorded."""
    trials = photo.Trials(result)
    rows = []
    for x, y, index, distance in photo.near_star_places(result, count):
        peak16, pixel = stated_peak(trials.luma, x, y)
        refused = trials.refused(x, y, peak16)[0]
        rows.append({"x": x, "y": y, "star": index, "distance": distance, "peak": round(peak16 / gate.CODES_PER_8_BIT, 3),
                     "refused": refused, "refusedAtHalf": trials.refused(x, y, 0.5 * peak16)[0],
                     "leastRefusedCodes": peak16 / gate.CODES_PER_8_BIT if refused else least_refused(trials, x, y, peak16),
                     "pixelNoiseCodes": round(pixel, 4)})
    by_distance = {str(d): [sum(r["refused"] for r in rows if r["distance"] == d), sum(r["distance"] == d for r in rows)]
                   for d in photo.NEAR_STAR_PX}
    least = [r["leastRefusedCodes"] for r in rows]
    every = None if any(v is None for v in least) else max(least, default=None)
    return {"stars": len({r["star"] for r in rows}), "trials": len(rows), "refused": sum(r["refused"] for r in rows),
            "refusedAtHalf": sum(r["refusedAtHalf"] for r in rows), "byDistance": by_distance, "everyTrialCodes": every,
            "atFourCodes": sum(r["peak"] <= gate.SENSITIVITY_PEAK_CODES for r in rows),
            "notRefused": [r for r in rows if not r["refused"]]}


def raised_trials(result: photo.StillRestoration, count: int, near: int) -> dict:
    """A sample, and 2 x 2 and 3 x 3 clusters, raised in every channel to the stated peak at each place
    (`stated_peak`) over the larger of the stored and the original's own sample (and, as AUDIT-B probed, by the stated
    peak on the stored sample alone, counted beside it), at the lattice (`count` places), beside `near` of the
    original's stars (photo.near_star_places) and on the kept edges of those stars (a sample of weight between 0 and 1
    in each of 8 directions); refused for any reason the gate gives (restore-4f). A place is not tried where it lies in
    a clipped region."""
    trials = photo.Trials(result)
    weight, clipped = result.context.kept.weight, result.context.field.clipped
    height, width = weight.shape
    families = {"lattice": [(x, y) for x, y in photo.painted_places(result, count)],
                "nearStar": [(x, y) for x, y, _, _ in photo.near_star_places(result, near)]}
    edge = []
    for _, _, index, distance in photo.near_star_places(result, near):
        if distance != photo.NEAR_STAR_PX[0]:
            continue
        star = result.context.field.stars[index]
        for k in range(photo.NEAR_STAR_DIRECTIONS):
            angle = 2 * math.pi * k / photo.NEAR_STAR_DIRECTIONS
            for step in np.arange(0.5, star.radius_px + stars_edge_px() + 1, 0.5):
                x = int(round(star.x_px + step * math.cos(angle)))
                y = int(round(star.y_px + step * math.sin(angle)))
                if 0 <= x < width and 0 <= y < height and 0 < weight[y, x] < 1:
                    edge.append((x, y))
                    break
    families["keptEdge"] = sorted(set(edge))
    out = {}
    # Pairs and a line of three single samples 2 px apart, along a row, a column and a diagonal (AUDIT-B's restore-4f
    # verdict: each lay on the other's ring), on the lattice and beside the stars
    for family in ("lattice", "nearStar"):
        for name, step, count in (("PairRow", (0, 2), 2), ("PairColumn", (2, 0), 2), ("PairDiagonal", (2, 2), 2),
                                  ("LineRow", (0, 2), 3)):
            rows = []
            for x, y in families[family]:
                span = [(x + k * step[1], y + k * step[0]) for k in range(count)]
                if any(not (0 <= px < width and 0 <= py < height) or clipped[py, px] for px, py in span):
                    continue
                peak16, _ = stated_peak(trials.luma, x, y)
                rows.append({"x": x, "y": y, "refused": trials.refused_spaced(x, y, peak16, step, count)})
            out[f"{family}{name}"] = {"trials": len(rows), "refused": sum(r["refused"] for r in rows),
                                      "refusedOnStored": None, "everyTrialCodes": None,
                                      "notRefused": [r for r in rows if not r["refused"]][:20]}
    # AUDIT-B's restore-4f v2 patterns: each raised sample with a partner 2 px and another 3 px out, and a row whose
    # samples touch none of each other (restore-4g)
    for family in ("lattice", "nearStar"):
        for name, offsets in (("Partners4", [(0, 0), (0, 2), (0, 3), (0, 5)]),
                              ("Partners8", [(0, c) for c in (0, 2, 3, 5, 6, 8, 9, 11)]),
                              ("Partners2D", [(r, c) for r in (0, 3) for c in (0, 2, 3, 5)]),
                              ("Partners0257", [(0, 0), (0, 2), (0, 5), (0, 7)])):
            rows = []
            for x, y in families[family]:
                span = [(x + dx, y + dy) for dy, dx in offsets]
                if any(not (0 <= px < width and 0 <= py < height) or clipped[py, px] for px, py in span):
                    continue
                peak16, _ = stated_peak(trials.luma, x, y)
                rows.append({"x": x, "y": y, "refused": trials.refused_offsets(x, y, peak16, offsets)})
            out[f"{family}{name}"] = {"trials": len(rows), "refused": sum(r["refused"] for r in rows),
                                      "refusedOnStored": None, "everyTrialCodes": None,
                                      "notRefused": [r for r in rows if not r["refused"]][:20]}
    for family, places in families.items():
        for size in (1, 2, 3):
            rows = []
            for x, y in places:
                if clipped[y:y + size, x:x + size].any():
                    continue
                peak16, _ = stated_peak(trials.luma, x, y)
                refused = trials.refused_raised(x, y, peak16, size)
                least = peak16 / gate.CODES_PER_8_BIT if refused else next(
                    (float(c) for c in LADDER if c * gate.CODES_PER_8_BIT > peak16
                     and trials.refused_raised(x, y, c * gate.CODES_PER_8_BIT, size)), None)
                rows.append({"x": x, "y": y, "refused": refused, "leastRefusedCodes": least,
                             "refusedOnStored": trials.refused_raised(x, y, peak16, size, over_original=False)})
            least = [r["leastRefusedCodes"] for r in rows]
            out[f"{family}{size}"] = {"trials": len(rows), "refused": sum(r["refused"] for r in rows),
                                      "everyTrialCodes": None if any(v is None for v in least) else max(least, default=None),
                                      "refusedOnStored": sum(r["refusedOnStored"] for r in rows),
                                      "notRefused": [r for r in rows if not r["refused"]][:20]}
    return out


def stars_edge_px() -> int:
    from services.media import stars

    return stars.FOOTPRINT_EDGE_PX


def view8(samples: np.ndarray) -> np.ndarray:
    return samples if samples.dtype == np.uint8 else np.round(samples / 257.0).astype(np.uint8)


def write_pictures(out: Path, name: str, still: decode.DecodedStill, result: photo.StillRestoration) -> None:
    before, after = view8(still.samples), view8(result.stored)
    Image.fromarray(before).save(out / f"{name}-before.png")
    Image.fromarray(after).save(out / f"{name}-after.png")
    if result.data is not None:
        (out / f"{name}-after-16bit.png").write_bytes(result.data)
    height, width = before.shape[:2]
    scale = min(1.0, SIDE_BY_SIDE_MAX_PX / (2 * width + 8))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    pair = Image.new("RGB", (2 * size[0] + 8, size[1]), (255, 255, 255))
    pair.paste(Image.fromarray(before).resize(size, Image.LANCZOS), (0, 0))
    pair.paste(Image.fromarray(after).resize(size, Image.LANCZOS), (size[0] + 8, 0))
    pair.save(out / f"{name}-side-by-side.png")


def review(photo_id: str, args: argparse.Namespace) -> dict:
    path = ROOT / ".cache" / "real-photos" / photo_id / f"{photo_id}.jpg"
    started = time.perf_counter()
    still = decode.decode_still(path)
    if args.crop:
        still = crop(still)
    result = photo.restore_still(still)
    restored = time.perf_counter() - started
    record = {"photo": photo_id, "crop": args.crop, "shape": list(still.samples.shape[:2]), "status": result.status,
              "rejection": result.rejection, "measured": {k: v for k, v in result.measured.items() if k not in ("colour", "noise")},
              "gate": result.gate,
              "unconfirmedPlaces": [[round(p.x_px), round(p.y_px), round(p.snr, 1)] for p in result.unconfirmed],
              "keptShare": round(float((result.context.protected >= 1).mean()), 4),
              "seconds": {k: round(v, 2) for k, v in result.seconds.items()}, "secondsToRestore": round(restored, 1),
              "megapixels": round(still.samples.shape[0] * still.samples.shape[1] / 1e6, 3), "load": load_average(),
              "liftSettings": {"targetMedianCode": tone.TARGET_MEDIAN_CODE, "quantisationBoundSteps": tone.QUANTISATION_BOUND_STEPS},
              "changedSettings": args.changed}
    record["painted"] = painted(result, args.trials)
    record["nearStars"] = near_stars(result, args.near_stars)
    record["raised"] = raised_trials(result, args.trials, args.near_stars)
    if args.pictures:
        suffix = ('' if args.target is None else f'-target{args.target}') + ('' if args.quantisation_bound is None else f'-q{args.quantisation_bound:g}')
        write_pictures(args.out, f"{photo_id}{'-crop' if args.crop else ''}{suffix}", still, result)
    return record




# =============================================================================================
# Clips (restore-7)
# =============================================================================================

CLIP_TYPES = {".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/mp4"}
# The job id the review's scratch store names its partial files with
REVIEW_JOB_ID = "job_" + "0" * 32
# An excerpt for a person to watch is this many seconds from the middle of the clip
EXCERPT_SEC = 3.0


def _ffprobe_json(args: list[str], path: Path) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-of", "json", *args, str(path)], capture_output=True, check=False)
    return json.loads(proc.stdout or b"{}")


def audio_facts(path: Path, probe: clip_io.ClipProbe) -> list[dict]:
    """Each audio stream as an ingest record states it: its index, codec, rate, channels, start, duration and packet
    hash."""
    streams = _ffprobe_json(["-show_streams", "-select_streams", "a"], path).get("streams") or []
    facts = []
    for k, stream in enumerate(probe.audio):
        found = streams[k] if k < len(streams) else {}
        facts.append({"index": stream.index, "codec": stream.codec, "sampleRateHz": int(found.get("sample_rate", 1)),
                      "channels": int(found.get("channels", 1)), "startPtsMs": 1000.0 * float(found.get("start_time", 0.0)),
                      "durationMs": 1000.0 * float(found.get("duration", 0.0)),
                      "sha256": clip_io.stream_packet_hash(path, f"0:{stream.index}", written_here=False)})
    return facts


def _frames_around(path: Path, middle: int, reach: int) -> list[tuple[np.ndarray, ...]]:
    probe = clip_io.probe_clip(path)
    kept = []
    with clip_io.ClipDecoder(path, probe) as decoder:
        for frame in decoder:
            if frame.index > middle + reach:
                break
            if frame.index >= middle - reach:
                kept.append(tuple(np.array(p) for p in frame.planes))
    return kept


def _clip_lattice(side: clip_frames.OriginalSide, count: int) -> list[tuple[int, int]]:
    """`count` places of a fixed lattice, as `photo.painted_places` lays them, clear of the frame's kept and clipped
    regions."""
    from scipy import ndimage

    occupied = (side.kept.weight > 0) | side.found.field.clipped
    clearance = ndimage.distance_transform_edt(~occupied) if occupied.any() else np.full(occupied.shape, np.inf)
    height, width = occupied.shape
    spacing = max(photo.PAINT_MIN_SPACING_PX, int((height * width / (4 * max(count, 1))) ** 0.5))
    lattice = [(x, y) for y in range(photo.PAINT_MARGIN_PX, height - photo.PAINT_MARGIN_PX, spacing)
               for x in range(photo.PAINT_MARGIN_PX, width - photo.PAINT_MARGIN_PX, spacing)
               if clearance[y, x] >= photo.PAINT_CLEARANCE_PX]
    if len(lattice) <= count:
        return lattice
    return [lattice[i] for i in np.linspace(0, len(lattice) - 1, count).round().astype(int)]


def clip_trials(settings: clip_frames.ClipSettings, side: clip_frames.OriginalSide, stored: tuple, count: int,
                stars_count: int) -> dict:
    """Painted trials on one stored frame of the clip: a star at the stated sensitivity (`stated_peak`) at each lattice
    place, and beside up to `stars_count` of the frame's own stars at photo.NEAR_STAR_PX along eight directions; each
    refused when the gate, which passes the frame as stored, refuses it painted."""
    clip = settings.colour
    scale = (2**clip.bits - 1) / 255.0
    luma = clip_frames.stored_luma(clip.stored(stored))
    yy, xx = np.mgrid[0:luma.shape[0], 0:luma.shape[1]].astype(np.float64)

    def refused(x: float, y: float, peak16: float) -> bool:
        patch = (peak16 / gate.CODES_PER_8_BIT) * scale * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * gate.STAR_SIGMA_PX**2))
        top = 2**clip.bits - 1
        painted_y = np.clip(np.round(stored[0].astype(np.float64) + patch), 0, top).astype(stored[0].dtype)
        return not clip_frames.gate_frame(settings, side, (painted_y, *stored[1:])).record["passed"]

    def least(x: float, y: float, peak16: float) -> float | None:
        """The least peak of LADDER, in codes of 255, above `peak16` that the place refuses, or None."""
        for codes in LADDER:
            if codes * gate.CODES_PER_8_BIT > peak16 and refused(x, y, codes * gate.CODES_PER_8_BIT):
                return float(codes)
        return None

    lattice = []
    for x, y in _clip_lattice(side, count):
        peak16, pixel = stated_peak(luma, x, y)
        refused_here = refused(x, y, peak16)
        lattice.append({"x": x, "y": y, "peak": round(peak16 / gate.CODES_PER_8_BIT, 3), "refused": refused_here,
                        "refusedAtHalf": refused(x, y, 0.5 * peak16), "pixelNoiseCodes": round(pixel, 4),
                        "leastRefusedCodes": peak16 / gate.CODES_PER_8_BIT if refused_here else least(x, y, peak16)})
    own = [s for s in side.found.field.stars if photo.PAINT_MARGIN_PX <= s.x_px < luma.shape[1] - photo.PAINT_MARGIN_PX
           and photo.PAINT_MARGIN_PX <= s.y_px < luma.shape[0] - photo.PAINT_MARGIN_PX]
    if len(own) > stars_count:
        own = [own[i] for i in np.linspace(0, len(own) - 1, stars_count).round().astype(int)]
    beside = []
    for star in own:
        for distance in photo.NEAR_STAR_PX:
            for k in range(photo.NEAR_STAR_DIRECTIONS):
                angle = 2 * np.pi * k / photo.NEAR_STAR_DIRECTIONS
                x, y = int(round(star.x_px + distance * np.cos(angle))), int(round(star.y_px + distance * np.sin(angle)))
                if side.found.field.clipped[y, x]:
                    continue
                peak16, _ = stated_peak(luma, x, y)
                refused_here = refused(x, y, peak16)
                beside.append({"distance": distance, "refused": refused_here,
                               "leastRefusedCodes": peak16 / gate.CODES_PER_8_BIT if refused_here else least(x, y, peak16)})
    every = [t["leastRefusedCodes"] for t in lattice + beside]
    return {"trials": len(lattice), "refused": sum(t["refused"] for t in lattice),
            "refusedAtHalf": sum(t["refusedAtHalf"] for t in lattice), "places": lattice,
            "everyTrialCodes": None if any(v is None for v in every) else max(every, default=None),
            "nearStars": {"stars": len(own), "trials": len(beside), "refused": sum(t["refused"] for t in beside),
                          "byDistance": {str(d): [sum(t["refused"] for t in beside if t["distance"] == d),
                                                  sum(t["distance"] == d for t in beside)] for d in photo.NEAR_STAR_PX}}}


def _frame_picture(path: Path, index: int, out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-noautorotate", "-i", str(path), "-vf", f"select=eq(n\\,{index})",
                    "-fps_mode", "passthrough", "-frames:v", "1", "-update", "1", str(out)], check=True)


def write_clip_pictures(out: Path, label: str, original: Path, stored: Path, middle: int, start_sec: float) -> None:
    """The middle frame before and after, side by side, and excerpts of EXCERPT_SEC: the original, the variant as
    stored, and the variant re-encoded at the design's crf 16 for the encoder's comparison."""
    before, after = out / f"{label}-before.png", out / f"{label}-after.png"
    _frame_picture(original, middle, before)
    _frame_picture(stored, middle, after)
    with Image.open(before) as left, Image.open(after) as right:
        width, height = left.size
        scale = min(1.0, SIDE_BY_SIDE_MAX_PX / (2 * width + 8))
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        pair = Image.new("RGB", (2 * size[0] + 8, size[1]), (255, 255, 255))
        pair.paste(left.convert("RGB").resize(size, Image.LANCZOS), (0, 0))
        pair.paste(right.convert("RGB").resize(size, Image.LANCZOS), (size[0] + 8, 0))
        pair.save(out / f"{label}-side-by-side.png")
    suffix = stored.suffix
    for name, source, codec in ((f"{label}-before{suffix}", original, ["-c", "copy"]),
                                (f"{label}-after{suffix}", stored, ["-c", "copy"]),
                                (f"{label}-after-crf16{suffix}", stored, ["-c:v", "libx264", "-crf", "16", "-preset", "medium",
                                                                          "-pix_fmt", "yuv420p", "-c:a", "copy"])):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-ss", f"{start_sec:.3f}", "-i", str(source), "-t",
                        f"{EXCERPT_SEC:.3f}", "-map", "0:v:0", "-map", "0:a?", *codec, "-map_metadata", "-1", str(out / name)],
                       check=True)


def review_clip(number: int, path: Path, args: argparse.Namespace) -> dict:
    """One clip through the job's own child function, in a scratch store of its own under the output folder (the
    original is linked there, never changed), then painted trials on its middle frame as stored, and pictures. The
    record names the clip by its number only."""
    import hashlib
    import shutil

    label = f"clip-{number}"
    scratch = args.out / f".store-{label}"
    if scratch.exists():
        shutil.rmtree(scratch)
    data_hash = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            data_hash.update(block)
    sha = data_hash.hexdigest()
    folder = scratch / sha[:2] / sha
    folder.mkdir(parents=True)
    original = folder / f"original{path.suffix.lower()}"
    try:
        os.link(path, original)
    except OSError:
        shutil.copyfile(path, original)
    (scratch / ".partial").mkdir()
    probe = clip_io.probe_clip(original)
    extension = path.suffix.lower().lstrip(".")
    facts = {"mimeType": CLIP_TYPES[path.suffix.lower()],
             "durationMs": 1000.0 * float(_ffprobe_json(["-show_format"], original).get("format", {}).get("duration", 0.0)),
             "audioStreams": audio_facts(original, probe)}
    partials = [str(scratch / ".partial" / f"{REVIEW_JOB_ID}-clip_encoded.{extension}.part"),
                str(scratch / ".partial" / f"{REVIEW_JOB_ID}-variant.{extension}.part")]
    stages: dict[str, list[float]] = {}

    def progress(stage: str, unit: str, done: int, total) -> None:
        now = time.perf_counter()
        stages.setdefault(stage, [now, now])[1] = now

    started = time.perf_counter()
    out = media_jobs.clip_restoration_in_child(str(scratch), str(folder), str(original), sha, REVIEW_JOB_ID,
                                               {"kind": "restoration"}, probe.display_orientation, facts, partials, progress,
                                               workers=args.workers)
    took = time.perf_counter() - started
    attempt = json.loads((folder / "attempts" / f"{out['attemptId']}.json").read_text())
    measured = attempt.get("measured") or {}
    record = {"clip": number, "status": attempt["status"], "rejection": attempt.get("rejection"),
              "failureReason": attempt.get("failureReason"), "frames": probe.frame_count,
              "megapixels": round(probe.video.width * probe.video.height / 1e6, 3), "pixelFormat": probe.video.pix_fmt,
              "measured": {k: v for k, v in measured.items() if k not in ("colour", "noise")}, "gate": attempt.get("gate"),
              "secondsByStage": {k: round(v[1] - v[0], 1) for k, v in stages.items()}, "secondsTotal": round(took, 1),
              "secondsPerFrame": round(took / max(1, probe.frame_count), 3), "workers": args.workers, "load": load_average()}
    if attempt["status"] == "ready":
        stored = folder / "variants" / out["variantId"] / f"variant.{extension}"
        record["storedBytesPerFrame"] = round(stored.stat().st_size / probe.frame_count)
        middle = probe.frame_count // 2
        reach = clip_frames.RUN_WINDOW
        originals, kept = _frames_around(original, middle, reach), _frames_around(stored, middle, reach)
        first = max(0, middle - reach)
        clip = clip_frames.clip_colour(probe.video)
        wanted = clip_frames.sample_indices(probe.frame_count)
        sampled = []
        with clip_io.ClipDecoder(original, probe) as decoder:
            for frame in decoder:
                if frame.index in wanted:
                    sampled.append(tuple(np.array(p) for p in frame.planes))
                    if len(sampled) == len(wanted):
                        break
        settings = clip_frames.measure_clip(clip, sampled)
        run = frame_pool.Run(middle, middle - first, 1, tuple(originals))
        frames, at = clip_frames._run_frames(settings, run, originals)
        side = clip_frames.original_side(settings, frames, at)
        trials_started = time.perf_counter()
        record["painted"] = clip_trials(settings, side, kept[middle - first], args.trials, args.near_stars)
        record["secondsForTrials"] = round(time.perf_counter() - trials_started, 1)
        if args.pictures:
            write_clip_pictures(args.out, label, original, stored, middle,
                                max(0.0, float(probe.frame_times[middle] * probe.time_base) - EXCERPT_SEC / 2))
    shutil.rmtree(scratch)
    return record


def review_clips(paths: list[Path], args: argparse.Namespace) -> None:
    for number, path in enumerate(paths, 1):
        record = review_clip(number, path, args)
        (args.out / f"clip-{number}.json").write_text(json.dumps(record, indent=1) + "\n")
        painted_trials = record.get("painted") or {}
        print(f"clip-{number}: {record['status']} ({record['rejection'] or record['failureReason'] or 'ready'}), "
              f"{record['frames']} frames, {record['secondsPerFrame']} s per frame with {record['workers']} workers; "
              f"painted {painted_trials.get('refused')} of {painted_trials.get('trials')}, beside stars "
              f"{(painted_trials.get('nearStars') or {}).get('refused')} of {(painted_trials.get('nearStars') or {}).get('trials')}",
              flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--photos", nargs="*", default=None)
    parser.add_argument("--clips", nargs="*", type=Path, default=None,
                        help="clips to restore through the job (restore-7), each named clip-<n> in the given order")
    parser.add_argument("--workers", type=int, default=frame_pool.default_workers(),
                        help="the clip pool's workers (agents' runs: 2; the product: half the cores)")
    parser.add_argument("--corpus", action="store_true")
    parser.add_argument("--crop", action="store_true")
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--near-stars", type=int, default=8, help="stars to paint beside, at each distance and direction")
    parser.add_argument("--out", type=Path, default=ROOT / ".cache" / "restore-review" / "restore-4")
    parser.add_argument("--no-pictures", dest="pictures", action="store_false")
    parser.add_argument("--target", type=int, default=None,
                        help="the lift's target code of 255 for the pictures a person compares (tone.TARGET_MEDIAN_CODE)")
    parser.add_argument("--quantisation-bound", type=float, default=None,
                        help="the lift's quantisation bound for the same comparison (tone.QUANTISATION_BOUND_STEPS)")
    parser.add_argument("--set", dest="settings", action="append", default=[], metavar="gate.NAME=VALUE",
                        help="measure with one of gate.py's settings changed (the record names it); not for pictures a person judges")
    args = parser.parse_args()
    args.changed = {}
    for item in args.settings:
        name, value = item.split("=", 1)
        module, constant = name.split(".", 1)
        if module != "gate" or not hasattr(gate, constant):
            parser.error(f"not a setting of gate.py: {name}")
        current = getattr(gate, constant)
        setattr(gate, constant, type(current)(float(value)) if isinstance(current, (int, float)) else value)
        args.changed[name] = getattr(gate, constant)
    # The three settings a person judges from pictures stay [proposed] in tone.py; these flags only make the
    # comparison pictures, and the record says which values made them
    if args.target is not None:
        tone.TARGET_MEDIAN_CODE = args.target
    if args.quantisation_bound is not None:
        tone.QUANTISATION_BOUND_STEPS = args.quantisation_bound
    args.out.mkdir(parents=True, exist_ok=True)
    if args.clips:
        review_clips(args.clips, args)
        return 0
    ids = [p["id"] for p in json.loads(CORPUS.read_text())["photos"]] if args.corpus else (args.photos or list(DESIGN_PHOTOS))
    for photo_id in ids:
        record = review(photo_id, args)
        name = f"{photo_id}{'-crop' if args.crop else ''}{'' if args.target is None else f'-target{args.target}'}{'' if args.quantisation_bound is None else f'-q{args.quantisation_bound:g}'}"
        (args.out / f"{name}.json").write_text(json.dumps(record, indent=1) + "\n")
        with (args.out / "review.jsonl").open("a") as handle:
            handle.write(json.dumps({k: v for k, v in record.items() if k != "painted"} | {"painted": {k: v for k, v in record["painted"].items() if k != "places"}}) + "\n")
        near = record["nearStars"]
        raised = record["raised"]
        raised_line = ", ".join(f"{k} {v['refused']}/{v['trials']}" + ("" if v["refusedOnStored"] is None else f" ({v['refusedOnStored']} on stored)")
                                for k, v in raised.items())
        print(f"{name}: {record['status']}, unconfirmed {record['gate']['unconfirmedPointCount']}, passes "
              f"{record['measured'].get('dissolvedPerPass')}, painted {record['painted']['refused']} of "
              f"{record['painted']['trials']} at the stated sensitivity, every place from "
              f"{record['painted']['everyTrialCodes']} codes, beside stars {near['refused']} of {near['trials']} "
              f"({near['refusedAtHalf']} at half, every place from {near['everyTrialCodes']} codes), raised: "
              f"{raised_line}, {record['secondsToRestore']} s", flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    raise SystemExit(main())
