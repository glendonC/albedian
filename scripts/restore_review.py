#!/usr/bin/env python3
"""Restore real photos end to end and write their review: counts, painted-star trials and before and after pictures
(spec restoration-for-every-upload, restore-4; REQ-RST-6.7, 6.12, REQ-RST-12).

Usage:
    uv run python scripts/restore_review.py [--photos rp-b-01 ...] [--corpus] [--crop] [--trials 40]
        [--out .cache/restore-review/restore-4] [--no-pictures]

Photos come from the real-photo corpus (`research/benchmarks/real_photos/corpus.json`, files in `.cache/real-photos/`);
the default is the design's five (rp-b-01 to rp-b-05), `--corpus` takes every photo. `--crop` takes the design's
768 px crop of the upper middle. For each photo it writes, under the output folder:
- `<id>[-crop].json`: the status, the attempt's measurements, the gate's record, seconds per step and the machine's
  load beside them, and the painted trials: how many places refused a star at the stated sensitivity, at half of
  it, and the least peak in codes of 255 each place refused (`everyTrialCodes`, the stated sensitivity reached).
- with pictures: `<id>[-crop]-before.png` and `-after.png` (8 bit views), `-after-16bit.png` (the stored file the
  gate ran on, written only when it is `ready`) and a side by side view no wider than 2400 px.
- `review.jsonl`: one line per photo, appended.

Nothing here is served: the pictures are for a person to judge (REQ-RST-12.3). No record holds a file name, a
capture time or a place: only the corpus id.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.media import decode, gate, photo, tone  # noqa: E402

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


def painted(result: photo.StillRestoration, count: int) -> dict:
    trials = photo.Trials(result)
    stored_luma = trials.luma
    peak_floor16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    places = []
    for x, y in photo.painted_places(result, count):
        window = stored_luma[max(0, y - 32):y + 32, max(0, x - 32):x + 32]
        from scipy import ndimage

        residual = window - ndimage.median_filter(window, size=5, mode="nearest")
        pixel = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
        peak16 = max(gate.SENSITIVITY_PIXEL_NOISES * pixel * gate.CODES_PER_8_BIT, peak_floor16)
        refused, snr = trials.refused(x, y, peak16)
        half, _ = trials.refused(x, y, 0.5 * peak16)
        least = peak16 / gate.CODES_PER_8_BIT if refused else None
        if not refused:
            for codes in LADDER:
                if codes * gate.CODES_PER_8_BIT > peak16 and trials.refused(x, y, codes * gate.CODES_PER_8_BIT)[0]:
                    least = float(codes)
                    break
        places.append({"x": x, "y": y, "peak": round(peak16 / gate.CODES_PER_8_BIT, 3), "refused": refused,
                       "refusedAtHalf": half, "snr": round(snr, 2), "leastRefusedCodes": least,
                       "pixelNoiseCodes": round(pixel, 4)})
    every = None if any(p["leastRefusedCodes"] is None for p in places) else max((p["leastRefusedCodes"] for p in places), default=None)
    return {"trials": len(places), "refused": sum(p["refused"] for p in places),
            "refusedAtHalf": sum(p["refusedAtHalf"] for p in places), "everyTrialCodes": every, "places": places}


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
    if args.pictures:
        suffix = ('' if args.target is None else f'-target{args.target}') + ('' if args.quantisation_bound is None else f'-q{args.quantisation_bound:g}')
        write_pictures(args.out, f"{photo_id}{'-crop' if args.crop else ''}{suffix}", still, result)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--photos", nargs="*", default=None)
    parser.add_argument("--corpus", action="store_true")
    parser.add_argument("--crop", action="store_true")
    parser.add_argument("--trials", type=int, default=40)
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
    ids = [p["id"] for p in json.loads(CORPUS.read_text())["photos"]] if args.corpus else (args.photos or list(DESIGN_PHOTOS))
    args.out.mkdir(parents=True, exist_ok=True)
    for photo_id in ids:
        record = review(photo_id, args)
        name = f"{photo_id}{'-crop' if args.crop else ''}{'' if args.target is None else f'-target{args.target}'}{'' if args.quantisation_bound is None else f'-q{args.quantisation_bound:g}'}"
        (args.out / f"{name}.json").write_text(json.dumps(record, indent=1) + "\n")
        with (args.out / "review.jsonl").open("a") as handle:
            handle.write(json.dumps({k: v for k, v in record.items() if k != "painted"} | {"painted": {k: v for k, v in record["painted"].items() if k != "places"}}) + "\n")
        print(f"{name}: {record['status']}, unconfirmed {record['gate']['unconfirmedPointCount']}, passes "
              f"{record['measured'].get('dissolvedPerPass')}, painted {record['painted']['refused']} of "
              f"{record['painted']['trials']} at the stated sensitivity, every place from "
              f"{record['painted']['everyTrialCodes']} codes, {record['secondsToRestore']} s", flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    raise SystemExit(main())
