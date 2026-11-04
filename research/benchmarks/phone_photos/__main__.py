"""Command line for the phone-photo benchmark.

    uv run python -m research.benchmarks.phone_photos run --suite ci --solver today-api
    uv run python -m research.benchmarks.phone_photos run --suite full --solver today-api --workers 8 --timeout 120

Writes, under .cache/solver-bench/<suite>-<solver>/ (ignored by git): summary.json, frames.jsonl
(one scored frame per line, with its spec and truth summary), summary.md, and with --save-frames
each frame's JPEG and truth. Everything written is labelled simulated.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import PIL
import scipy

from research.benchmarks.phone_photos import score, solvers, suite

ROOT = Path(__file__).resolve().parents[3]


def _environment() -> dict:
    from PIL import features

    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pillow": PIL.__version__,
        "libjpegTurbo": features.version("libjpeg_turbo"),
        "machine": platform.machine(),
        "system": platform.system(),
        "cpuCount": os.cpu_count(),
    }


def run(args: argparse.Namespace) -> int:
    specs = suite.build_suite(args.suite, seed=args.seed)
    if args.only:
        wanted = set(args.only.split(","))
        specs = [s for s in specs if s.category in wanted or s.kind in wanted or s.camera in wanted]
    if args.limit:
        specs = specs[: args.limit]
    out = Path(args.out) if args.out else ROOT / ".cache" / "solver-bench" / f"{args.suite}-{args.solver}"
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = None
    if args.save_frames:
        frames_dir = out / "frames"
        frames_dir.mkdir(exist_ok=True)
    tasks = [(s.to_dict(), args.solver, args.timeout, str(frames_dir) if frames_dir else None) for s in specs]
    started = time.perf_counter()
    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=score.init_worker) as pool:
        for i, result in enumerate(pool.map(score.run_one, tasks), 1):
            results.append(result)
            s = result["score"]
            print(f"[{i}/{len(tasks)}] {s['frame_id']} {s['kind']} {s['category']} {s['camera']}: {s['outcome']} "
                  f"({s['detections']} detections, solve {s['elapsed_s']:.1f} s, render {result['renderS']:.1f} s)", flush=True)
    scores = [score.FrameScore(**r["score"]) for r in results]
    summary = score.summarize(scores, args.solver)
    summary.update({
        "suite": args.suite,
        "seed": args.seed,
        "timeoutS": args.timeout,
        "workers": args.workers,
        "wallS": time.perf_counter() - started,
        "environment": _environment(),
    })
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    with (out / "frames.jsonl").open("w", encoding="utf-8") as handle:
        for r in results:
            handle.write(json.dumps(r) + "\n")
    (out / "summary.md").write_text(score.to_markdown(summary) + "\n", encoding="utf-8")
    print(score.to_markdown(summary))
    print(f"Written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.benchmarks.phone_photos", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="Render a suite, run a solver on every frame and score it.")
    p.add_argument("--suite", choices=["ci", "full"], default="ci")
    p.add_argument("--solver", choices=sorted(solvers.SOLVERS), default="today-api")
    p.add_argument("--seed", type=int, default=suite.DEFAULT_SEED)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p.add_argument("--timeout", type=float, default=120.0, help="Seconds a solver may spend on one frame.")
    p.add_argument("--limit", type=int, default=0, help="Run only the first N frames.")
    p.add_argument("--only", default="", help="Comma-separated categories, kinds or cameras to keep.")
    p.add_argument("--out", default="", help="Output folder (default .cache/solver-bench/<suite>-<solver>).")
    p.add_argument("--save-frames", action="store_true", help="Also write each frame's JPEG and truth.")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
